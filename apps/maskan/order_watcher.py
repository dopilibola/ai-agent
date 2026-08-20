"""OrderWatcher — turns Maskan backend order state into funnel transitions.

This is the half of the funnel the tenant cannot cause, only observe. Three
facts live entirely in the Django backend and reach us no other way:

* **payment** — Payme calls the backend's merchant webhook; the order flips to
  `payment_status=paid` and the backend routes it to the cemetery's caretaker
  group;
* **work started** — a caretaker taps "Qabul qildim" in that Telegram group
  (`status` → `accepted`/`progress`);
* **work finished** — the caretaker uploads before/after photos and an admin
  confirms them (`status` → `completed`, or `rejected`).

So this job polls the orders we are tracking and, when a status differs from the
one we last reacted to, calls the matching `funnel.on_*` transition. That is why
the agent has no "mark paid" tool: the only thing that can make an order paid is
a real payment.

Idempotency is the whole game here. `MaskanLead.last_order_status` /
`last_payment_status` record what the client has already been told, so a poll
that sees an unchanged status does nothing, and a crash between "message sent"
and "status stored" costs at most one duplicate — never a silent miss.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.maskan import funnel
from apps.maskan.api_client import ApiError, order_status
from apps.maskan.config import MaskanConfig, config as default_config
from apps.maskan.models import MaskanLead
from apps.maskan.repository import get_repository

logger = logging.getLogger(__name__)

# Backend statuses that mean a caretaker has taken the job on. `submitted`
# (photos uploaded, awaiting the admin's confirmation) is deliberately included:
# from the client's point of view the work is still in progress, and they should
# not hear about our internal review step.
_WORKING = {"accepted", "progress", "submitted"}


@dataclass
class OrderWatcher:
    """Polls the Django backend for order changes. Wire into Tenant.sync_jobs."""

    cfg: MaskanConfig = default_config
    name: str = "maskan-order-watcher"

    @property
    def interval_seconds(self) -> int:
        return self.cfg.watcher_interval_seconds

    async def run_once(self) -> None:
        if not self.cfg.api_configured:
            logger.debug("Maskan API not configured — order watcher idle.")
            return

        repo = get_repository()
        try:
            leads = await repo.leads_with_open_orders(limit=self.cfg.watcher_batch_size)
        except Exception:
            logger.exception("Maskan watcher: failed to load tracked leads")
            return
        if not leads:
            return

        by_order = {int(lead.django_order_id): lead for lead in leads if lead.django_order_id}
        try:
            rows = await order_status(list(by_order), cfg=self.cfg)
        except ApiError as exc:
            # A backend blip is normal; the next tick retries the same set.
            logger.warning("Maskan watcher: status poll failed — %s", exc.detail)
            return

        for row in rows:
            lead = by_order.get(int(row.get("id") or 0))
            if lead is None:
                continue
            try:
                await self._apply(lead, row)
            except Exception:
                logger.exception(
                    "Maskan watcher: applying order %s to lead %s failed",
                    row.get("id"), lead.id,
                )

    async def _apply(self, lead: MaskanLead, order: dict) -> None:
        """React to one order row, at most one transition per poll.

        Ordered payment-first: an order can go from awaiting+pending to
        paid+accepted between two polls, and the client should hear "payment
        received" before "work started". The next tick delivers the second step.
        """
        repo = get_repository()
        status = str(order.get("status") or "")
        payment = str(order.get("payment_status") or "")

        # 1. Payment landed (Payme webhook) — this is the big one.
        if payment == "paid" and lead.last_payment_status != "paid":
            await funnel.on_payment_received(lead, order)
            return

        # Nothing else can be true before the money is in.
        if payment != "paid":
            if payment and payment != lead.last_payment_status:
                await repo.update_lead(lead.id, last_payment_status=payment)
            return

        if status == lead.last_order_status:
            return

        # 2. A caretaker took the job on.
        if status in _WORKING and lead.last_order_status not in _WORKING:
            await funnel.on_work_started(lead, order)
            return

        # 3. Done — photos confirmed by the admin.
        if status == "completed":
            await funnel.on_work_completed(lead, order)
            return

        # 4. Rejected — staff own it from here.
        if status == "rejected":
            await funnel.on_order_rejected(lead, order)
            return

        # Any other movement (e.g. submitted→accepted) is internal: record it so
        # we don't re-evaluate the same transition on every tick.
        await repo.update_lead(lead.id, last_order_status=status)
