"""PaymentWatcher — reacts to invoices the payment webhook marked paid.

`payments_api.py` runs as its own web process: it can hear Payme and Uzum, but
it has no Telethon client and no funnel context, so all it does is flip a
`maskan_payments` row to `paid`. This job — inside the bot process, where the
client's chat actually lives — is the other half: it claims those rows and runs
the ordinary `funnel.on_payment_received` transition, so a payment taken through
our own merchant account reaches the client exactly like a backend one.

The claim (`repository.claim_paid_payments`) stamps `notified_at` as it hands
the row over, so the client is thanked once even if this process restarts
mid-tick. A payment whose chat has no lead is logged and left alone rather than
guessed at — money is never dropped silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.maskan import funnel, payments
from apps.maskan.config import MaskanConfig, config as default_config
from apps.maskan.repository import get_repository

logger = logging.getLogger(__name__)


@dataclass
class PaymentWatcher:
    """Polls our own payment invoices. Wire into Tenant.sync_jobs."""

    cfg: MaskanConfig = default_config
    name: str = "maskan-payment-watcher"

    @property
    def interval_seconds(self) -> int:
        return self.cfg.payment_watcher_interval

    async def run_once(self) -> None:
        if not payments.any_provider_enabled(self.cfg):
            logger.debug("No payment provider configured — payment watcher idle.")
            return
        repo = get_repository()
        rows = await repo.claim_paid_payments(limit=20)
        for payment in rows:
            try:
                await self._handle(payment)
            except Exception:
                # Un-stamp so the next tick retries: better a duplicate thank-you
                # than a client who paid and heard nothing.
                logger.exception("Payment %s: notification failed", payment.id)
                try:
                    await repo.update_payment(payment.id, notified_at=None)
                except Exception:
                    logger.debug("Payment %s: could not reset notified_at", payment.id)

    async def _handle(self, payment) -> None:
        repo = get_repository()
        # Flip the order itself first: it is what the staff work queue
        # (`open_orders`) reads, and it must say "paid" even if the lead lookup
        # or the client message below fails.
        if payment.order_id:
            await repo.mark_order_paid(payment.order_id)
        lead = await repo.get_active_lead_by_chat(payment.chat_id)
        if lead is None:
            logger.warning(
                "Payment %s paid (%s tiyin) but chat %s has no open lead",
                payment.id, payment.amount_tiyin, payment.chat_id,
            )
            return
        if payment.order_id and not lead.django_order_id:
            lead = await repo.update_lead(lead.id, django_order_id=int(payment.order_id)) or lead
        logger.info(
            "Payment %s paid via %s — advancing lead %s",
            payment.id, payment.provider, lead.id,
        )
        await funnel.on_payment_received(
            lead, {"total": payments.tiyin_to_som(payment.amount_tiyin)}
        )
