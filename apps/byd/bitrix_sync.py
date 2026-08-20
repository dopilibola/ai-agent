"""BitrixFunnelSync — the pull half of the Bitrix24 integration.

A `sync.SyncJob` that polls the funnel's deal pipeline and applies what
operators did **in Bitrix** back onto the local funnel, so the CRM kanban is a
real control surface, not just a mirror:

* drag to «Не дозвон» / «Переговоры»          → same as the call-outcome buttons
* drag to «Консультация назначена»            → confirmation to the client +
  arrival reminders (needs Программа + Дата заезда filled in the deal card —
  otherwise the operator gets a Telegram nag and the move is retried next poll)
* drag to «Запрос предоплаты»                 → payment link + reminders
* drag to «Бронь»                             → mark paid: voucher PDF etc.
* drag to «Подтверждение брони»               → arrival-confirmation chain
* drag to «Успешно реализовано» (WON)         → post-sale chain
* drag to any failure stage                   → lead closed, touches cancelled
* edit Программа / Дата заезда / Дата рождения in the card → pulled onto the
  lead (amounts recomputed while unpaid, reminders re-anchored)

Echo suppression: `lead.bitrix_stage_id` is the stage as last synced (either
direction). Only a differing live STAGE_ID counts as an operator move; after
applying we anchor the ref, so our own pushes never bounce back.

Deals created directly in Bitrix are ignored — they have no Telegram chat to
message. Runs in the same process as the userbot + DealScheduler (it sends
client messages through the funnel context).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from apps.byd import bitrix, funnel
from apps.byd.config import CLINIC_TZ, BydConfig, config as default_config
from apps.byd.models import (
    STAGE_BOOKED,
    STAGE_CONFIRMED,
    STAGE_CONSULT,
    STAGE_DONE,
    STAGE_NEGOTIATION,
    STAGE_NEW,
    STAGE_NO_ANSWER,
    STAGE_PREPAYMENT,
    BydLead,
)
from apps.byd.repository import get_repository

logger = logging.getLogger(__name__)

# Re-nag an operator about missing deal-card fields at most this often.
NAG_INTERVAL = timedelta(minutes=30)
# Refresh the STAGE_ID → name directory this often (close-reason wording).
STAGE_NAMES_TTL = timedelta(minutes=10)


def _parse_uf_date(value) -> Optional[date]:
    """Bitrix returns date UFs as '' or ISO with the portal offset
    ('2026-08-10T00:00:00+03:00') — the calendar date is the first 10 chars."""
    raw = str(value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


@dataclass
class BitrixFunnelSync:
    cfg: BydConfig = default_config
    name: str = "byd-bitrix-sync"

    _nagged_at: dict[int, datetime] = field(default_factory=dict)
    _stage_names: dict[str, str] = field(default_factory=dict)
    _stage_names_at: Optional[datetime] = None

    @property
    def interval_seconds(self) -> int:
        return self.cfg.bitrix_sync_interval_seconds

    async def run_once(self) -> None:
        if not self.cfg.bitrix_enabled:
            return
        repo = get_repository()
        leads = await repo.list_bitrix_synced_leads()
        if not leads:
            return
        try:
            deals = await bitrix.fetch_tracked_deals(
                [int(l.bitrix_deal_id) for l in leads], self.cfg
            )
        except Exception:
            logger.exception("Bitrix poll failed")
            return
        for lead in leads:
            deal = deals.get(int(lead.bitrix_deal_id or 0))
            if deal is None:
                logger.warning(
                    "BYD lead %s: Bitrix deal %s not found (deleted?)",
                    lead.id, lead.bitrix_deal_id,
                )
                continue
            try:
                await self._pull_lead(lead, deal)
            except Exception:
                logger.exception(
                    "Failed to pull Bitrix deal %s for lead %s",
                    lead.bitrix_deal_id, lead.id,
                )

    # ----- per-lead -----------------------------------------------------------

    async def _pull_lead(self, lead: BydLead, deal: dict) -> None:
        # Fields first, so a stage move that needs them sees the fresh values.
        await funnel.apply_remote_details(
            lead.id,
            program_code=str(deal.get(bitrix.UF_PROGRAM) or "").strip() or None,
            arrival=_parse_uf_date(deal.get(bitrix.UF_ARRIVAL)),
            dob=_parse_uf_date(deal.get(bitrix.UF_DOB)),
        )
        lead = await get_repository().get_lead(lead.id) or lead

        remote = str(deal.get("STAGE_ID") or "")
        if not remote or remote == (lead.bitrix_stage_id or ""):
            return
        await self._apply_stage(lead, deal, remote)

    async def _apply_stage(self, lead: BydLead, deal: dict, remote: str) -> None:
        repo = get_repository()
        target = bitrix.local_stage_for(remote, self.cfg)
        semantic = str(deal.get("STAGE_SEMANTIC_ID") or "P")

        if target is None:
            if semantic == "F":
                await repo.set_bitrix_refs(lead.id, stage_id=remote)
                await funnel.close_from_bitrix(
                    lead.id, await self._stage_label(remote)
                )
            elif semantic == "S":
                await repo.set_bitrix_refs(lead.id, stage_id=remote)
                await funnel.mark_completed(lead.id)
            else:
                logger.warning(
                    "BYD lead %s: unmapped in-progress Bitrix stage %r — ignoring",
                    lead.id, remote,
                )
                await repo.set_bitrix_refs(lead.id, stage_id=remote)
            return

        if target == lead.current_stage:
            await repo.set_bitrix_refs(lead.id, stage_id=remote)
            return

        if target <= STAGE_NEW or target < lead.current_stage:
            # Regressions have no funnel semantics (money/vouchers can't be
            # unsent) — record the ref so we don't reprocess, and let the next
            # local transition push the funnel's real stage forward again.
            logger.info(
                "BYD lead %s: Bitrix stage moved back to %r (local %s) — not applied",
                lead.id, remote, lead.current_stage,
            )
            await repo.set_bitrix_refs(lead.id, stage_id=remote)
            return

        # Field guards: stage 4/5 are meaningless without the program/arrival.
        # Deliberately NOT anchoring the ref on a deferred move — the poll
        # retries until the operator fills the card (nag throttled).
        if target in (STAGE_CONSULT, STAGE_PREPAYMENT) and not (
            lead.program_code and (lead.arrival_date or target == STAGE_PREPAYMENT)
        ):
            await self._nag_missing_fields(lead)
            return
        if target == STAGE_PREPAYMENT and lead.prepayment_amount is None:
            await self._nag_missing_fields(lead)
            return

        await repo.set_bitrix_refs(lead.id, stage_id=remote)
        logger.info(
            "BYD lead %s: applying Bitrix stage %r → local %s", lead.id, remote, target
        )
        if target == STAGE_NO_ANSWER:
            await funnel.record_call_outcome(lead.id, reached=False)
        elif target == STAGE_NEGOTIATION:
            await funnel.record_call_outcome(lead.id, reached=True)
        elif target == STAGE_CONSULT:
            await funnel.schedule_consultation(
                lead_id=lead.id,
                program_code=lead.program_code or "",
                arrival=lead.arrival_date,
                date_of_birth=lead.date_of_birth,
            )
        elif target == STAGE_PREPAYMENT:
            await funnel.request_prepayment(lead.id)
        elif target == STAGE_BOOKED:
            await funnel.mark_paid(lead.id)
        elif target == STAGE_CONFIRMED:
            await funnel.advance_to_confirmation(lead.id)
        elif target == STAGE_DONE:
            await funnel.mark_completed(lead.id)

    # ----- helpers ------------------------------------------------------------

    async def _nag_missing_fields(self, lead: BydLead) -> None:
        now = datetime.now(CLINIC_TZ)
        last = self._nagged_at.get(lead.id)
        if last is not None and now - last < NAG_INTERVAL:
            return
        self._nagged_at[lead.id] = now
        url = bitrix.deal_url(int(lead.bitrix_deal_id or 0), self.cfg)
        await funnel.notify_operators(
            "⚠️ <b>Сделка в Bitrix ждёт данных</b>\n\n"
            f"Вы передвинули сделку клиента <b>{lead.name or '—'}</b>, но в "
            "карточке не заполнены «Программа (7/14/21)» и/или «Дата заезда». "
            "Заполните поля — и переход применится автоматически.\n\n"
            + (f'<a href="{url}">Открыть сделку в Bitrix</a>' if url else "")
        )

    async def _stage_label(self, stage_id: str) -> str:
        now = datetime.now(CLINIC_TZ)
        if (
            self._stage_names_at is None
            or now - self._stage_names_at > STAGE_NAMES_TTL
        ):
            try:
                self._stage_names = await bitrix.stage_names(self.cfg)
                self._stage_names_at = now
            except Exception:
                logger.debug("Failed to refresh Bitrix stage names", exc_info=True)
        return self._stage_names.get(stage_id, stage_id)
