"""Postgres data access for the Maskan funnel.

The single data layer for the bot runtime (tools, callbacks, scheduler, order
watcher) and the admin panel: leads plus the scheduled-task queue. Pure DB
access — no message templates, no channel/notifier/HTTP calls (those live in
`apps.maskan.funnel` and `apps.maskan.api_client`).

Import-light on purpose (sqlalchemy + db.engine + apps.maskan.{models,config})
so the admin panel can import it without the agent/Telegram runtime.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from apps.maskan.config import MASKAN_TZ
from apps.maskan.models import (
    ORDER_ACCEPTED,
    ORDER_COMPLETED,
    ORDER_PAID,
    ORDER_PENDING,
    PAYMENT_NEW,
    PAYMENT_PAID,
    STAGE_NEW,
    STATUS_ACTIVE,
    STATUS_CLOSED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_FAILED,
    TASK_PENDING,
    TASK_RUNNING,
    MaskanCemetery,
    MaskanGrave,
    MaskanLead,
    MaskanOrder,
    MaskanPayment,
    MaskanScheduledTask,
    MaskanService,
)
from db.engine import get_sessionmaker

TENANT = "maskan"


def _now() -> datetime:
    """Current instant in Tashkent time (stored tz-aware = unambiguous)."""
    return datetime.now(MASKAN_TZ)


async def _log_outcome(
    lead: Optional[MaskanLead], *, event: str, meta: dict
) -> None:
    """Label the conversation corpus with what the funnel did next.

    A dialogue is only trainable if you know how it ended — paid, went cold,
    handed to a human. Stage/status changes are that label, written against the
    same thread the conversation lives in. Best-effort: never breaks a
    transition (see db/training.py).
    """
    if lead is None:
        return
    try:
        from db import training

        chat_id = int(getattr(lead, "chat_id", 0) or 0)
        await training.log_event(
            tenant_id=TENANT,
            chat_id=chat_id,
            thread_id=f"{TENANT}:customer:{chat_id}",
            channel="customer",
            role="outcome",
            text=event,
            meta={
                **meta,
                "lead_id": int(getattr(lead, "id", 0) or 0),
                "status": meta.get("status", getattr(lead, "status", "")),
            },
        )
    except Exception:  # pragma: no cover
        pass


class MaskanRepository:
    # ===== leads ============================================================

    async def create_lead(
        self,
        *,
        chat_id: int,
        name: str = "",
        phone: str = "",
        request: str = "",
        tg_username: str = "",
        stage: int = STAGE_NEW,
    ) -> MaskanLead:
        now = _now()
        lead = MaskanLead(
            tenant_id=TENANT,
            chat_id=int(chat_id),
            name=name or "",
            phone=phone or "",
            request=request or "",
            tg_username=tg_username or "",
            current_stage=int(stage),
            status=STATUS_ACTIVE,
            service_codes=[],
            stage_entered_at=now,
            created_at=now,
            updated_at=now,
        )
        async with get_sessionmaker()() as session:
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
            return lead

    async def get_lead(self, lead_id) -> Optional[MaskanLead]:
        async with get_sessionmaker()() as session:
            return await session.get(MaskanLead, int(lead_id))

    async def get_active_lead_by_chat(self, chat_id: int) -> Optional[MaskanLead]:
        """The chat's open deal (newest first). Closed leads are skipped so a
        returning client starts a fresh one rather than reviving a finished job."""
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            return await session.scalar(
                select(MaskanLead)
                .where(
                    MaskanLead.tenant_id == TENANT,
                    MaskanLead.chat_id == int(chat_id),
                    MaskanLead.status != STATUS_CLOSED,
                )
                .order_by(MaskanLead.id.desc())
                .limit(1)
            )

    async def get_latest_lead_by_chat(self, chat_id: int) -> Optional[MaskanLead]:
        """The chat's most recent lead regardless of status — used by the
        do-not-contact check, which must see closed leads too."""
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            return await session.scalar(
                select(MaskanLead)
                .where(
                    MaskanLead.tenant_id == TENANT,
                    MaskanLead.chat_id == int(chat_id),
                )
                .order_by(MaskanLead.id.desc())
                .limit(1)
            )

    async def get_lead_by_order(self, order_id: int) -> Optional[MaskanLead]:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            return await session.scalar(
                select(MaskanLead)
                .where(
                    MaskanLead.tenant_id == TENANT,
                    MaskanLead.django_order_id == int(order_id),
                )
                .order_by(MaskanLead.id.desc())
                .limit(1)
            )

    async def update_lead(self, lead_id, **fields) -> Optional[MaskanLead]:
        if not fields:
            return await self.get_lead(lead_id)
        from sqlalchemy import update

        fields["updated_at"] = _now()
        async with get_sessionmaker()() as session:
            await session.execute(
                update(MaskanLead).where(MaskanLead.id == int(lead_id)).values(**fields)
            )
            await session.commit()
            return await session.get(MaskanLead, int(lead_id))

    async def advance_stage(self, lead_id, new_stage: int, **fields) -> Optional[MaskanLead]:
        """Move a lead to `new_stage`, stamping `stage_entered_at` (the anchor
        the SLA checks and the admin panel key off) plus any field updates."""
        from sqlalchemy import select, update

        now = _now()
        values = dict(fields)
        values.update(
            current_stage=int(new_stage),
            stage_entered_at=now,
            updated_at=now,
        )
        async with get_sessionmaker()() as session:
            # Column select (not session.get) so the ORM identity map stays empty
            # and the post-commit get below returns the *updated* row.
            previous = await session.scalar(
                select(MaskanLead.current_stage).where(MaskanLead.id == int(lead_id))
            )
            await session.execute(
                update(MaskanLead).where(MaskanLead.id == int(lead_id)).values(**values)
            )
            await session.commit()
            lead = await session.get(MaskanLead, int(lead_id))
        await _log_outcome(
            lead,
            event="stage",
            meta={"from_stage": int(previous or 0), "to_stage": int(new_stage)},
        )
        return lead

    async def set_status(
        self, lead_id, status: str, close_reason: Optional[str] = None
    ) -> Optional[MaskanLead]:
        values: dict = {"status": status}
        if close_reason is not None:
            values["close_reason"] = close_reason
        lead = await self.update_lead(lead_id, **values)
        await _log_outcome(
            lead,
            event="status",
            meta={"status": status, "reason": close_reason or ""},
        )
        return lead

    async def list_leads(self, *, limit: int = 200) -> list[MaskanLead]:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(MaskanLead)
                .where(MaskanLead.tenant_id == TENANT)
                .order_by(MaskanLead.updated_at.desc())
                .limit(limit)
            )
            return list(rows)

    async def find_leads(self, query: str, *, limit: int = 10) -> list[MaskanLead]:
        """Partial match over name, phone, the deceased's name, the cemetery and
        the client's own words — how an operator actually remembers a case."""
        from sqlalchemy import or_, select

        q = f"%{(query or '').strip()}%"
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(MaskanLead)
                .where(
                    MaskanLead.tenant_id == TENANT,
                    or_(
                        MaskanLead.name.ilike(q),
                        MaskanLead.phone.ilike(q),
                        MaskanLead.grave_label.ilike(q),
                        MaskanLead.cemetery_label.ilike(q),
                        MaskanLead.request.ilike(q),
                    ),
                )
                .order_by(MaskanLead.updated_at.desc())
                .limit(limit)
            )
            return list(rows)

    # Statuses we stop polling on. `completed`/`rejected` come from the backend;
    # `expired` is ours — stamped when the payment window closes, so an order
    # nobody ever paid for doesn't sit in the poll set forever and crowd out live
    # ones. (If such an order is paid much later, the backend still routes it to
    # the caretaker group as normal; we simply stop narrating it in chat, which
    # is exactly what the expiry message told the client.)
    TERMINAL_ORDER_STATUSES = ("completed", "rejected", "expired")

    async def leads_with_open_orders(self, *, limit: int = 40) -> list[MaskanLead]:
        """Leads whose Django order hasn't reached a terminal state yet — the
        watcher's poll set.

        Ordered oldest-`updated_at` first, which is round-robin fairness rather
        than a stale-first bias: acting on a lead refreshes its `updated_at` and
        sends it to the back of the queue, so a backlog larger than `limit` still
        gets serviced evenly instead of starving the tail.
        """
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(MaskanLead)
                .where(
                    MaskanLead.tenant_id == TENANT,
                    MaskanLead.django_order_id.is_not(None),
                    MaskanLead.last_order_status.notin_(self.TERMINAL_ORDER_STATUSES),
                )
                .order_by(MaskanLead.updated_at.asc())
                .limit(limit)
            )
            return list(rows)

    # ===== scheduled tasks ==================================================

    async def enqueue_tasks(self, rows: list[dict]) -> int:
        """Upsert scheduled-task rows keyed by `dedup_key`.

        ON CONFLICT it *resets* the row to pending with the new time (not DO
        NOTHING): a transition both cancels a lead's pending tasks and enqueues
        the next stage's plan, so a retried transition must be able to revive the
        rows it just cancelled. It is also what lets a chat-level follow-up timer
        be pushed back by each new inbound message. A row currently `running`
        (claimed by a poller) is left alone, so we never reset a task mid-execute
        into a double fire.

        Each row dict must carry: lead_id, chat_id, action_type, stage,
        scheduled_for, dedup_key, and optionally payload.
        """
        if not rows:
            return 0
        from sqlalchemy.dialects.postgresql import insert

        now = _now()
        values = [
            {
                "tenant_id": TENANT,
                "lead_id": int(r["lead_id"]),
                "chat_id": int(r["chat_id"]),
                "action_type": r["action_type"],
                "stage": int(r.get("stage", 0)),
                "payload": r.get("payload") or {},
                "scheduled_for": r["scheduled_for"],
                "status": TASK_PENDING,
                "attempts": 0,
                "dedup_key": r["dedup_key"],
                "created_at": now,
            }
            for r in rows
        ]
        ins = insert(MaskanScheduledTask).values(values)
        stmt = ins.on_conflict_do_update(
            index_elements=["dedup_key"],
            set_={
                "status": TASK_PENDING,
                "scheduled_for": ins.excluded.scheduled_for,
                "action_type": ins.excluded.action_type,
                "stage": ins.excluded.stage,
                "payload": ins.excluded.payload,
                "attempts": 0,
                "last_error": None,
            },
            where=(MaskanScheduledTask.status != TASK_RUNNING),
        )
        async with get_sessionmaker()() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)

    # ----- catalogue (our own price list, cemeteries, graves, orders) -------

    async def list_services(self) -> list[MaskanService]:
        """The active price list, in display order."""
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(MaskanService)
                .where(MaskanService.active.is_(True))
                .order_by(MaskanService.sort, MaskanService.id)
            )
            return list(rows)

    async def services_by_codes(self, codes: list[str]) -> list[MaskanService]:
        """Resolve service codes to rows, preserving the caller's order and
        silently dropping unknown codes — the tool reports what it could not
        find rather than inventing a price."""
        from sqlalchemy import select

        wanted = [str(c).strip() for c in codes if str(c).strip()]
        if not wanted:
            return []
        async with get_sessionmaker()() as session:
            rows = list(
                await session.scalars(
                    select(MaskanService).where(
                        MaskanService.code.in_(wanted),
                        MaskanService.active.is_(True),
                    )
                )
            )
        by_code = {r.code: r for r in rows}
        return [by_code[c] for c in wanted if c in by_code]

    async def upsert_service(self, code: str, **fields) -> MaskanService:
        """Insert-or-update by `code` — how the seed/import script reconciles."""
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            row = await session.scalar(
                select(MaskanService).where(MaskanService.code == code)
            )
            if row is None:
                row = MaskanService(code=code, updated_at=_now(), **fields)
                session.add(row)
            else:
                for key, value in fields.items():
                    setattr(row, key, value)
                row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return row

    # Uzbek names carry an oʻ/gʻ apostrophe that nobody types the same way: the
    # official list uses U+2018/U+2019, phones produce U+02BB, keyboards produce
    # a plain "\'". Matching them literally means "Chig\'atoy" misses
    # "Chig\u2018atoy" — the client is then told, wrongly, that their cemetery is
    # out of the service area. Both sides of the comparison drop the character
    # entirely, which also makes "Bozsuv" find "Boʻzsuv".
    _APOSTROPHES = "'\u2018\u2019\u02bb\u0060\u00b4"

    @classmethod
    def _fold(cls, text: str) -> str:
        return "".join(ch for ch in (text or "") if ch not in cls._APOSTROPHES)

    async def search_cemeteries(
        self, query: str, *, limit: int = 8
    ) -> list[MaskanCemetery]:
        """Fuzzy cemetery lookup over name/city/district.

        An empty query returns the whole (short) list, which is what the agent
        wants when a client says "I don't remember the name".
        """
        from sqlalchemy import String, func, literal, or_, select

        def folded(column):
            return func.translate(func.lower(column), literal(self._APOSTROPHES), literal(""))

        stmt = select(MaskanCemetery).where(MaskanCemetery.active.is_(True))
        term = (query or "").strip()
        if term:
            like = f"%{self._fold(term).lower()}%"
            stmt = stmt.where(
                or_(
                    folded(MaskanCemetery.name_uz).like(like),
                    folded(MaskanCemetery.name_ru).like(like),
                    folded(MaskanCemetery.city).like(like),
                    folded(MaskanCemetery.district).like(like),
                )
            )
        async with get_sessionmaker()() as session:
            rows = list(
                await session.scalars(
                    stmt.order_by(MaskanCemetery.name_uz).limit(limit)
                )
            )
            if rows or not term:
                return rows
            # Nothing matched as a substring. Before declaring the cemetery out
            # of the service area — which ends the sale — try a fuzzy pass over
            # the whole (short) list: clients write "Do'mbrobod" for
            # "Dombirobod", "Qoraqamish" for "Qora Qamish". Cheap at ~120 rows.
            everything = list(
                await session.scalars(
                    select(MaskanCemetery)
                    .where(MaskanCemetery.active.is_(True))
                    .order_by(MaskanCemetery.name_uz)
                )
            )
        return self._fuzzy_cemeteries(term, everything, limit=limit)

    @classmethod
    def _fuzzy_cemeteries(
        cls, term: str, rows: list[MaskanCemetery], *, limit: int,
        threshold: float = 0.78,
    ) -> list[MaskanCemetery]:
        """Closest names by edit-distance ratio, best first.

        "qabristoni" is dropped from both sides — every row carries it, so it
        only dilutes the score.
        """
        from difflib import SequenceMatcher

        def key(text: str) -> str:
            return cls._fold(text).lower().replace("qabristoni", "").strip()

        needle = key(term)
        if len(needle) < 3:
            return []
        # 0.78 is where real spelling variants ("Do'mbrobod" -> "Dombirobod",
        # 0.95; "kamolon" -> "Komolon", 0.86) sit clear of the noise floor
        # ("Samarqand" -> "Ramadan", 0.62). Anything below is left unmatched on
        # purpose: `find_cemetery` then shows the known list and lets the client
        # pick, which beats confidently quoting the wrong cemetery.
        scored = []
        for row in rows:
            score = SequenceMatcher(None, needle, key(row.name_uz)).ratio()
            if score >= threshold:
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _, row in scored[:limit]]

    async def suggest_cemeteries(
        self, query: str, *, limit: int = 3
    ) -> list[MaskanCemetery]:
        """Near-misses: close enough to ask about, not close enough to assume.

        Between the confident band (>= 0.78, returned by `search_cemeteries`)
        and noise sits the transliteration gap — "dumbrabad" for "Dombirobod"
        scores 0.63. Telling that client "not found" loses a real sale; silently
        picking for them risks the wrong cemetery. So they come back as
        suggestions the agent must put as a question.
        """
        from sqlalchemy import select

        term = (query or "").strip()
        if not term:
            return []
        async with get_sessionmaker()() as session:
            rows = list(
                await session.scalars(
                    select(MaskanCemetery).where(MaskanCemetery.active.is_(True))
                )
            )
        close = self._fuzzy_cemeteries(term, rows, limit=limit, threshold=0.55)
        confident = {c.id for c in self._fuzzy_cemeteries(term, rows, limit=limit)}
        return [c for c in close if c.id not in confident]

    async def get_cemetery(self, cemetery_id) -> Optional[MaskanCemetery]:
        async with get_sessionmaker()() as session:
            return await session.get(MaskanCemetery, int(cemetery_id))

    async def upsert_cemetery(self, name_uz: str, **fields) -> MaskanCemetery:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            row = await session.scalar(
                select(MaskanCemetery).where(MaskanCemetery.name_uz == name_uz)
            )
            if row is None:
                row = MaskanCemetery(name_uz=name_uz, updated_at=_now(), **fields)
                session.add(row)
            else:
                for key, value in fields.items():
                    setattr(row, key, value)
                row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return row

    async def list_graves(self, chat_id: int) -> list[MaskanGrave]:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(MaskanGrave)
                .where(MaskanGrave.chat_id == int(chat_id))
                .order_by(MaskanGrave.id)
            )
            return list(rows)

    async def get_grave(self, grave_id) -> Optional[MaskanGrave]:
        async with get_sessionmaker()() as session:
            return await session.get(MaskanGrave, int(grave_id))

    async def create_grave(self, *, chat_id: int, name: str, **fields) -> MaskanGrave:
        grave = MaskanGrave(chat_id=int(chat_id), name=name, created_at=_now(), **fields)
        async with get_sessionmaker()() as session:
            session.add(grave)
            await session.commit()
            await session.refresh(grave)
            return grave

    async def update_grave(self, grave_id, **fields) -> Optional[MaskanGrave]:
        """Correct a registered grave (spelling of the name, years, sector).

        Clients type the deceased's name from memory and phone keyboards mangle
        it; the caretaker has to find that name on a headstone, so a correction
        must be able to land after the fact.
        """
        if not fields:
            return await self.get_grave(grave_id)
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            await session.execute(
                update(MaskanGrave).where(MaskanGrave.id == int(grave_id)).values(**fields)
            )
            await session.commit()
            return await session.get(MaskanGrave, int(grave_id))

    async def create_order(
        self,
        *,
        chat_id: int,
        items: list[dict],
        total: int,
        grave_id: Optional[int] = None,
        grave_label: str = "",
        cemetery_label: str = "",
        frequency: str = "once",
    ) -> MaskanOrder:
        now = _now()
        order = MaskanOrder(
            chat_id=int(chat_id),
            grave_id=int(grave_id) if grave_id else None,
            grave_label=grave_label,
            cemetery_label=cemetery_label,
            items=items,
            total=int(total),
            frequency=frequency,
            status=ORDER_PENDING,
            created_at=now,
            updated_at=now,
        )
        async with get_sessionmaker()() as session:
            session.add(order)
            await session.commit()
            await session.refresh(order)
            return order

    async def get_order(self, order_id) -> Optional[MaskanOrder]:
        async with get_sessionmaker()() as session:
            return await session.get(MaskanOrder, int(order_id))

    async def list_orders(self, chat_id: int, *, limit: int = 10) -> list[MaskanOrder]:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(MaskanOrder)
                .where(MaskanOrder.chat_id == int(chat_id))
                .order_by(MaskanOrder.id.desc())
                .limit(limit)
            )
            return list(rows)

    async def list_orders_by_status(
        self, statuses: list[str], *, limit: int = 30
    ) -> list[MaskanOrder]:
        """Staff view: what is waiting to be dispatched or finished."""
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(MaskanOrder)
                .where(MaskanOrder.status.in_(statuses))
                .order_by(MaskanOrder.id.desc())
                .limit(limit)
            )
            return list(rows)

    async def get_order_by_payment(self, payment_id) -> Optional[MaskanOrder]:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            return await session.scalar(
                select(MaskanOrder).where(MaskanOrder.payment_id == int(payment_id))
            )

    async def update_order(self, order_id, **fields) -> Optional[MaskanOrder]:
        from sqlalchemy import update

        fields["updated_at"] = _now()
        async with get_sessionmaker()() as session:
            await session.execute(
                update(MaskanOrder).where(MaskanOrder.id == int(order_id)).values(**fields)
            )
            await session.commit()
            return await session.get(MaskanOrder, int(order_id))

    async def mark_order_paid(self, order_id) -> Optional[MaskanOrder]:
        return await self.update_order(order_id, status=ORDER_PAID, paid_at=_now())

    async def mark_order_accepted(
        self, order_id, caretaker: str = ""
    ) -> Optional[MaskanOrder]:
        fields: dict = {"status": ORDER_ACCEPTED, "accepted_at": _now()}
        if caretaker:
            fields["caretaker"] = caretaker
        return await self.update_order(order_id, **fields)

    async def mark_order_completed(self, order_id) -> Optional[MaskanOrder]:
        return await self.update_order(
            order_id, status=ORDER_COMPLETED, completed_at=_now()
        )

    # ----- payments (own merchant account) ---------------------------------

    async def create_payment(
        self,
        *,
        chat_id: int,
        amount_tiyin: int,
        lead_id: Optional[int] = None,
        order_id: Optional[int] = None,
        detail: Optional[dict] = None,
    ) -> MaskanPayment:
        """Open an invoice. Its `id` is what the checkout links carry, and what
        the providers name when they call back."""
        now = _now()
        payment = MaskanPayment(
            tenant_id=TENANT,
            chat_id=int(chat_id),
            lead_id=int(lead_id) if lead_id else None,
            order_id=int(order_id) if order_id else None,
            amount_tiyin=int(amount_tiyin),
            state=PAYMENT_NEW,
            detail=detail or {},
            created_at=now,
            updated_at=now,
        )
        async with get_sessionmaker()() as session:
            session.add(payment)
            await session.commit()
            await session.refresh(payment)
            return payment

    async def get_payment(self, payment_id) -> Optional[MaskanPayment]:
        async with get_sessionmaker()() as session:
            return await session.get(MaskanPayment, int(payment_id))

    async def get_payment_by_txn(
        self, provider: str, txn_id: str
    ) -> Optional[MaskanPayment]:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            return await session.scalar(
                select(MaskanPayment).where(
                    MaskanPayment.provider == provider,
                    MaskanPayment.provider_txn_id == str(txn_id),
                )
            )

    async def update_payment(self, payment_id, **fields) -> Optional[MaskanPayment]:
        from sqlalchemy import update

        fields["updated_at"] = _now()
        async with get_sessionmaker()() as session:
            await session.execute(
                update(MaskanPayment)
                .where(MaskanPayment.id == int(payment_id))
                .values(**fields)
            )
            await session.commit()
            return await session.get(MaskanPayment, int(payment_id))

    async def claim_paid_payments(self, *, limit: int = 20) -> list[MaskanPayment]:
        """Claim invoices the webhook marked paid but nobody has acted on yet.

        Stamping `notified_at` inside the claim is what makes the client message
        and the operator alert fire exactly once, even if the watcher restarts
        mid-tick — the same FOR UPDATE SKIP LOCKED shape as the task queue.
        """
        from sqlalchemy import select, update

        pending = (
            select(MaskanPayment.id)
            .where(
                MaskanPayment.tenant_id == TENANT,
                MaskanPayment.state == PAYMENT_PAID,
                MaskanPayment.notified_at.is_(None),
            )
            .order_by(MaskanPayment.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(MaskanPayment)
            .where(MaskanPayment.id.in_(pending.scalar_subquery()))
            .values(notified_at=_now())
            .returning(MaskanPayment)
            .execution_options(synchronize_session=False)
        )
        async with get_sessionmaker()() as session:
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            await session.commit()
            return rows

    async def claim_due_tasks(self, *, limit: int = 20) -> list[MaskanScheduledTask]:
        """Atomically claim up to `limit` pending tasks whose time has come.

        `FOR UPDATE SKIP LOCKED` on the inner select means two pollers never grab
        the same row; the outer UPDATE flips them to `running` and RETURNs them,
        so the caller owns them until it marks done/failed.
        """
        from sqlalchemy import func, select, update

        due = (
            select(MaskanScheduledTask.id)
            .where(
                MaskanScheduledTask.tenant_id == TENANT,
                MaskanScheduledTask.status == TASK_PENDING,
                MaskanScheduledTask.scheduled_for <= func.now(),
            )
            .order_by(MaskanScheduledTask.scheduled_for)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(MaskanScheduledTask)
            .where(MaskanScheduledTask.id.in_(due.scalar_subquery()))
            .values(status=TASK_RUNNING, attempts=MaskanScheduledTask.attempts + 1)
            .returning(MaskanScheduledTask)
            .execution_options(synchronize_session=False)
        )
        async with get_sessionmaker()() as session:
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            await session.commit()
            return rows

    async def mark_task_done(self, task_id) -> None:
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            await session.execute(
                update(MaskanScheduledTask)
                .where(MaskanScheduledTask.id == int(task_id))
                .values(status=TASK_DONE, executed_at=_now(), last_error=None)
            )
            await session.commit()

    async def mark_task_retry_or_failed(
        self, task_id, *, error: str, max_attempts: int, backoff_seconds: int = 120
    ) -> None:
        """A claimed task that errored: back to `pending` with a backed-off time
        if attempts remain, else parked in `failed` for manual review."""
        from sqlalchemy import update

        task = await self.get_task(task_id)
        if task is None:
            return
        if task.attempts >= max_attempts:
            new_status = TASK_FAILED
            scheduled_for = task.scheduled_for
        else:
            new_status = TASK_PENDING
            scheduled_for = _now() + timedelta(seconds=backoff_seconds * task.attempts)
        async with get_sessionmaker()() as session:
            await session.execute(
                update(MaskanScheduledTask)
                .where(MaskanScheduledTask.id == int(task_id))
                .values(
                    status=new_status,
                    scheduled_for=scheduled_for,
                    last_error=(error or "")[:2000],
                )
            )
            await session.commit()

    async def get_task(self, task_id) -> Optional[MaskanScheduledTask]:
        async with get_sessionmaker()() as session:
            return await session.get(MaskanScheduledTask, int(task_id))

    async def reclaim_stale_running(self, *, older_than_seconds: int = 600) -> int:
        """Reset tasks stuck in `running` (a poller crashed mid-execute) back to
        `pending` so they fire again. Restart-safety net."""
        from sqlalchemy import update

        cutoff = _now() - timedelta(seconds=older_than_seconds)
        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(MaskanScheduledTask)
                .where(
                    MaskanScheduledTask.tenant_id == TENANT,
                    MaskanScheduledTask.status == TASK_RUNNING,
                    MaskanScheduledTask.created_at < cutoff,
                )
                .values(status=TASK_PENDING)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def cancel_pending_for_lead(
        self, lead_id, *, exclude_actions: Optional[set[str]] = None
    ) -> int:
        """Cancel every still-pending task for a lead (used on stage transitions
        and terminal states). `exclude_actions` keeps named action types alive —
        e.g. the seasonal memorial nudge, which is deliberately independent of
        where the deal currently sits."""
        from sqlalchemy import update

        conds = [
            MaskanScheduledTask.tenant_id == TENANT,
            MaskanScheduledTask.lead_id == int(lead_id),
            MaskanScheduledTask.status == TASK_PENDING,
        ]
        if exclude_actions:
            conds.append(MaskanScheduledTask.action_type.notin_(tuple(exclude_actions)))
        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(MaskanScheduledTask).where(*conds).values(status=TASK_CANCELLED)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def cancel_pending_actions(self, lead_id, actions: set[str]) -> int:
        """Cancel a lead's pending tasks of specific action types only."""
        from sqlalchemy import update

        if not actions:
            return 0
        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(MaskanScheduledTask)
                .where(
                    MaskanScheduledTask.tenant_id == TENANT,
                    MaskanScheduledTask.lead_id == int(lead_id),
                    MaskanScheduledTask.status == TASK_PENDING,
                    MaskanScheduledTask.action_type.in_(tuple(actions)),
                )
                .values(status=TASK_CANCELLED)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def cancel_pending_for_chat(self, chat_id: int) -> int:
        """Cancel every still-pending task addressed to a chat — any lead's rows
        plus the lead-less chat-level rows (lead_id=0). The do-not-contact flow
        uses this: nothing scheduled may ever reach the chat again."""
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(MaskanScheduledTask)
                .where(
                    MaskanScheduledTask.tenant_id == TENANT,
                    MaskanScheduledTask.chat_id == int(chat_id),
                    MaskanScheduledTask.status == TASK_PENDING,
                )
                .values(status=TASK_CANCELLED)
            )
            await session.commit()
            return int(result.rowcount or 0)


_repository: Optional[MaskanRepository] = None


def get_repository() -> MaskanRepository:
    global _repository
    if _repository is None:
        _repository = MaskanRepository()
    return _repository
