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
    STAGE_NEW,
    STATUS_ACTIVE,
    STATUS_CLOSED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_FAILED,
    TASK_PENDING,
    TASK_RUNNING,
    MaskanLead,
    MaskanScheduledTask,
)
from db.engine import get_sessionmaker

TENANT = "maskan"


def _now() -> datetime:
    """Current instant in Tashkent time (stored tz-aware = unambiguous)."""
    return datetime.now(MASKAN_TZ)


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
        from sqlalchemy import update

        now = _now()
        values = dict(fields)
        values.update(
            current_stage=int(new_stage),
            stage_entered_at=now,
            updated_at=now,
        )
        async with get_sessionmaker()() as session:
            await session.execute(
                update(MaskanLead).where(MaskanLead.id == int(lead_id)).values(**values)
            )
            await session.commit()
            return await session.get(MaskanLead, int(lead_id))

    async def set_status(
        self, lead_id, status: str, close_reason: Optional[str] = None
    ) -> Optional[MaskanLead]:
        values: dict = {"status": status}
        if close_reason is not None:
            values["close_reason"] = close_reason
        return await self.update_lead(lead_id, **values)

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
