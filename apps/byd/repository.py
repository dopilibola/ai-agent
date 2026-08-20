"""Postgres data access for the BYD funnel.

The single data layer for both the bot runtime (tools, callbacks, scheduler) and
(later) the admin panel: leads, the scheduled-task queue, programs, and operator
tasks. Pure DB access — no message templates, no channel/notifier calls (those
live in `apps.byd.funnel`).

Import-light on purpose (sqlalchemy + db.engine + apps.byd.{models,config}) so the
admin panel can import it without the agent/Telegram runtime.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from apps.byd.config import CLINIC_TZ
from apps.byd.models import (
    STATUS_ACTIVE,
    STATUS_CLOSED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_FAILED,
    TASK_PENDING,
    TASK_RUNNING,
    BydLead,
    BydOperatorTask,
    BydProgram,
    BydScheduledTask,
)
from db.engine import get_sessionmaker

TENANT = "byd"


def _now() -> datetime:
    """Current instant in the clinic timezone (stored as tz-aware = unambiguous)."""
    return datetime.now(CLINIC_TZ)


class BydRepository:
    # ===== leads ============================================================

    async def create_lead(
        self,
        *,
        chat_id: int,
        name: str = "",
        request: str = "",
        city: str = "",
        phone: str = "",
        tg_username: str = "",
        stage: int = 1,
    ) -> BydLead:
        now = _now()
        lead = BydLead(
            tenant_id=TENANT,
            chat_id=int(chat_id),
            current_stage=stage,
            status=STATUS_ACTIVE,
            name=name or "",
            request=request or "",
            city=city or "",
            phone=phone or "",
            tg_username=tg_username or "",
            stage_entered_at=now,
            created_at=now,
            updated_at=now,
        )
        async with get_sessionmaker()() as session:
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
        return lead

    async def get_lead(self, lead_id) -> Optional[BydLead]:
        try:
            lid = int(lead_id)
        except (TypeError, ValueError):
            return None
        async with get_sessionmaker()() as session:
            return await session.get(BydLead, lid)

    async def get_active_lead_by_chat(self, chat_id: int) -> Optional[BydLead]:
        """The most recent non-closed lead for a chat (the one the funnel acts
        on). Returns None if the chat has no open deal."""
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            return await session.scalar(
                select(BydLead)
                .where(
                    BydLead.tenant_id == TENANT,
                    BydLead.chat_id == int(chat_id),
                    BydLead.status != STATUS_CLOSED,
                )
                .order_by(BydLead.created_at.desc())
                .limit(1)
            )

    async def get_latest_lead_by_chat(self, chat_id: int) -> Optional[BydLead]:
        """The most recent lead for a chat INCLUDING closed ones — how the
        do-not-contact flag (which lives on a closed lead) is looked up."""
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            return await session.scalar(
                select(BydLead)
                .where(BydLead.tenant_id == TENANT, BydLead.chat_id == int(chat_id))
                .order_by(BydLead.created_at.desc())
                .limit(1)
            )

    async def update_lead(self, lead_id, **fields) -> Optional[BydLead]:
        if not fields:
            return await self.get_lead(lead_id)
        from sqlalchemy import update

        fields["updated_at"] = _now()
        async with get_sessionmaker()() as session:
            await session.execute(
                update(BydLead).where(BydLead.id == int(lead_id)).values(**fields)
            )
            await session.commit()
            return await session.get(BydLead, int(lead_id))

    async def advance_stage(self, lead_id, new_stage: int, **fields) -> Optional[BydLead]:
        """Move a lead to `new_stage`, stamping `stage_entered_at` (the anchor
        the SLA logic + audit key off) and any extra field updates atomically."""
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
                update(BydLead).where(BydLead.id == int(lead_id)).values(**values)
            )
            await session.commit()
            return await session.get(BydLead, int(lead_id))

    async def mark_operator_acted(self, lead_id, outcome: str) -> None:
        """Record the first operator action (and the latest call outcome). The
        `operator_first_action_at` stamp is what lets the +10min reminder and
        +3h escalation skip a lead an operator already handled."""
        from sqlalchemy import update

        now = _now()
        async with get_sessionmaker()() as session:
            # Always record the latest outcome…
            await session.execute(
                update(BydLead)
                .where(BydLead.id == int(lead_id))
                .values(last_call_outcome=outcome, updated_at=now)
            )
            # …but only stamp the FIRST action time once (guarded update — keeps
            # the original "touched at" so the 3h escalation measures from it).
            await session.execute(
                update(BydLead)
                .where(
                    BydLead.id == int(lead_id),
                    BydLead.operator_first_action_at.is_(None),
                )
                .values(operator_first_action_at=now)
            )
            await session.commit()

    async def set_status(
        self, lead_id, status: str, close_reason: Optional[str] = None
    ) -> Optional[BydLead]:
        values: dict = {"status": status, "updated_at": _now()}
        if close_reason is not None:
            values["close_reason"] = close_reason
        return await self.update_lead(lead_id, **values)

    async def assign_voucher_number(self, lead_id) -> int:
        """Allocate the next voucher number from the Postgres sequence and stamp
        it on the lead. Sequence = no duplicate numbers under concurrency."""
        from sqlalchemy import text, update

        async with get_sessionmaker()() as session:
            number = int(await session.scalar(text("SELECT nextval('byd_voucher_seq')")))
            await session.execute(
                update(BydLead)
                .where(BydLead.id == int(lead_id))
                .values(voucher_number=number, updated_at=_now())
            )
            await session.commit()
        return number

    async def list_leads(self, *, limit: int = 200) -> list[BydLead]:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(BydLead)
                .where(BydLead.tenant_id == TENANT)
                .order_by(BydLead.updated_at.desc())
                .limit(limit)
            )
            return list(rows)

    async def set_bitrix_refs(
        self,
        lead_id,
        *,
        contact_id: Optional[int] = None,
        deal_id: Optional[int] = None,
        stage_id: Optional[str] = None,
    ) -> None:
        """Stamp Bitrix mirror ids on a lead (only the ones given)."""
        fields: dict = {}
        if contact_id is not None:
            fields["bitrix_contact_id"] = int(contact_id)
        if deal_id is not None:
            fields["bitrix_deal_id"] = int(deal_id)
        if stage_id is not None:
            fields["bitrix_stage_id"] = str(stage_id)
        if fields:
            await self.update_lead(lead_id, **fields)

    async def get_lead_by_deal(self, deal_id: int) -> Optional[BydLead]:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            return await session.scalar(
                select(BydLead)
                .where(BydLead.tenant_id == TENANT, BydLead.bitrix_deal_id == int(deal_id))
                .order_by(BydLead.id.desc())
                .limit(1)
            )

    async def list_bitrix_synced_leads(self) -> list[BydLead]:
        """Every lead that has a Bitrix deal and is not terminally closed —
        the set the pull job diffs against the live pipeline."""
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(BydLead).where(
                    BydLead.tenant_id == TENANT,
                    BydLead.bitrix_deal_id.is_not(None),
                    BydLead.status != STATUS_CLOSED,
                )
            )
            return list(rows)

    async def find_leads(self, query: str, *, limit: int = 10) -> list[BydLead]:
        """Operator-facing fuzzy lookup by name/phone/city (ILIKE)."""
        from sqlalchemy import or_, select

        like = f"%{query.strip()}%"
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(BydLead)
                .where(
                    BydLead.tenant_id == TENANT,
                    or_(
                        BydLead.name.ilike(like),
                        BydLead.phone.ilike(like),
                        BydLead.city.ilike(like),
                        BydLead.request.ilike(like),
                    ),
                )
                .order_by(BydLead.updated_at.desc())
                .limit(limit)
            )
            return list(rows)

    # ===== scheduled tasks (the keystone) ===================================

    async def enqueue_tasks(self, rows: list[dict]) -> int:
        """Upsert scheduled-task rows keyed by `dedup_key`.

        ON CONFLICT it *resets* the row to pending with the new time (not DO
        NOTHING): a transition both cancels a lead's pending tasks and re-enqueues
        its plan, so a double-tapped/retried transition must be able to revive the
        rows it just cancelled — DO NOTHING would leave them cancelled. A row
        currently `running` (claimed by a poller) is left alone (the `where`), so
        we never reset a task mid-execute into a double-fire.

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
        ins = insert(BydScheduledTask).values(values)
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
            where=(BydScheduledTask.status != TASK_RUNNING),
        )
        async with get_sessionmaker()() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)

    async def claim_due_tasks(self, *, limit: int = 20) -> list[BydScheduledTask]:
        """Atomically claim up to `limit` pending tasks whose time has come.

        `FOR UPDATE SKIP LOCKED` on the inner select means two poller processes
        (e.g. byd-all + a stray restart) never grab the same row; the outer
        UPDATE flips them to `running` and RETURNs them so the caller owns them
        exclusively until it marks done/failed.
        """
        from sqlalchemy import func, select, update

        due = (
            select(BydScheduledTask.id)
            .where(
                BydScheduledTask.tenant_id == TENANT,
                BydScheduledTask.status == TASK_PENDING,
                BydScheduledTask.scheduled_for <= func.now(),
            )
            .order_by(BydScheduledTask.scheduled_for)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(BydScheduledTask)
            .where(BydScheduledTask.id.in_(due.scalar_subquery()))
            .values(status=TASK_RUNNING, attempts=BydScheduledTask.attempts + 1)
            .returning(BydScheduledTask)
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
                update(BydScheduledTask)
                .where(BydScheduledTask.id == int(task_id))
                .values(status=TASK_DONE, executed_at=_now(), last_error=None)
            )
            await session.commit()

    async def mark_task_retry_or_failed(
        self, task_id, *, error: str, max_attempts: int, backoff_seconds: int = 120
    ) -> None:
        """A claimed task that errored: return it to `pending` with a backed-off
        time if attempts remain, else park it in `failed` for manual review."""
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
                update(BydScheduledTask)
                .where(BydScheduledTask.id == int(task_id))
                .values(
                    status=new_status,
                    scheduled_for=scheduled_for,
                    last_error=(error or "")[:2000],
                )
            )
            await session.commit()

    async def get_task(self, task_id) -> Optional[BydScheduledTask]:
        async with get_sessionmaker()() as session:
            return await session.get(BydScheduledTask, int(task_id))

    async def reclaim_stale_running(self, *, older_than_seconds: int = 600) -> int:
        """Reset tasks stuck in `running` (a poller crashed mid-execute) back to
        `pending` so they fire again. Restart-safety net."""
        from sqlalchemy import update

        cutoff = _now() - timedelta(seconds=older_than_seconds)
        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(BydScheduledTask)
                .where(
                    BydScheduledTask.tenant_id == TENANT,
                    BydScheduledTask.status == TASK_RUNNING,
                    BydScheduledTask.created_at < cutoff,
                )
                .values(status=TASK_PENDING)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def cancel_pending_for_lead(
        self, lead_id, *, exclude_actions: Optional[set[str]] = None
    ) -> int:
        """Cancel every still-pending task for a lead (used on stage transitions
        and terminal states). `exclude_actions` keeps named action types alive
        (e.g. don't cancel the just-enqueued next-stage tasks).

        `bitrix_*` rows always survive: they are CRM-mirror deliveries, not
        client touches — a close must still be pushed to Bitrix, and a queued
        timeline comment must not be lost to a stage transition."""
        from sqlalchemy import not_, update

        conds = [
            BydScheduledTask.tenant_id == TENANT,
            BydScheduledTask.lead_id == int(lead_id),
            BydScheduledTask.status == TASK_PENDING,
            not_(BydScheduledTask.action_type.like("bitrix_%")),
        ]
        if exclude_actions:
            conds.append(BydScheduledTask.action_type.notin_(tuple(exclude_actions)))
        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(BydScheduledTask).where(*conds).values(status=TASK_CANCELLED)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def cancel_pending_actions(self, lead_id, actions: set[str]) -> int:
        """Cancel a lead's pending tasks of specific action types only (used to
        re-anchor date-driven reminders when the arrival date moves)."""
        from sqlalchemy import update

        if not actions:
            return 0
        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(BydScheduledTask)
                .where(
                    BydScheduledTask.tenant_id == TENANT,
                    BydScheduledTask.lead_id == int(lead_id),
                    BydScheduledTask.status == TASK_PENDING,
                    BydScheduledTask.action_type.in_(tuple(actions)),
                )
                .values(status=TASK_CANCELLED)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def cancel_pending_for_chat(self, chat_id: int) -> int:
        """Cancel every still-pending task addressed to a chat — any lead's rows
        plus the lead-less chat-level rows (lead_id=0). The do-not-contact flow
        uses this: nothing scheduled may ever reach the chat again. `bitrix_*`
        rows survive — they go to the CRM, not the chat, and the CRM must still
        record the do-not-contact close."""
        from sqlalchemy import not_, update

        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(BydScheduledTask)
                .where(
                    BydScheduledTask.tenant_id == TENANT,
                    BydScheduledTask.chat_id == int(chat_id),
                    BydScheduledTask.status == TASK_PENDING,
                    not_(BydScheduledTask.action_type.like("bitrix_%")),
                )
                .values(status=TASK_CANCELLED)
            )
            await session.commit()
            return int(result.rowcount or 0)

    # ===== programs =========================================================

    async def get_program(self, code: str) -> Optional[BydProgram]:
        async with get_sessionmaker()() as session:
            return await session.get(BydProgram, str(code))

    async def list_programs(self, *, active_only: bool = True) -> list[BydProgram]:
        from sqlalchemy import select

        stmt = select(BydProgram)
        if active_only:
            stmt = stmt.where(BydProgram.active.is_(True))
        stmt = stmt.order_by(BydProgram.days)
        async with get_sessionmaker()() as session:
            return list(await session.scalars(stmt))

    async def upsert_program(
        self, *, code: str, title: str, days: int, price: int, active: bool = True
    ) -> None:
        from sqlalchemy.dialects.postgresql import insert

        now = _now()
        values = {
            "code": str(code),
            "title": title,
            "days": int(days),
            "price": int(price),
            "active": active,
            "created_at": now,
            "updated_at": now,
        }
        stmt = insert(BydProgram).values(**values).on_conflict_do_update(
            index_elements=["code"],
            set_={k: values[k] for k in values if k not in ("code", "created_at")},
        )
        async with get_sessionmaker()() as session:
            await session.execute(stmt)
            await session.commit()

    # ===== operator tasks ===================================================

    async def create_operator_task(
        self,
        *,
        lead_id: int,
        chat_id: int,
        kind: str,
        title: str,
        checklist: Optional[list] = None,
        due_at: Optional[datetime] = None,
    ) -> BydOperatorTask:
        task = BydOperatorTask(
            tenant_id=TENANT,
            lead_id=int(lead_id),
            chat_id=int(chat_id),
            kind=kind,
            title=title,
            status="open",
            checklist=checklist or [],
            due_at=due_at,
            created_at=_now(),
        )
        async with get_sessionmaker()() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
        return task

    async def get_operator_task(self, task_id) -> Optional[BydOperatorTask]:
        async with get_sessionmaker()() as session:
            return await session.get(BydOperatorTask, int(task_id))

    async def set_operator_task_bitrix_id(self, task_id, bitrix_task_id: int) -> None:
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            await session.execute(
                update(BydOperatorTask)
                .where(BydOperatorTask.id == int(task_id))
                .values(bitrix_task_id=int(bitrix_task_id))
            )
            await session.commit()

    async def close_operator_task(self, task_id) -> bool:
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(BydOperatorTask)
                .where(BydOperatorTask.id == int(task_id), BydOperatorTask.status == "open")
                .values(status="done", done_at=_now())
            )
            await session.commit()
            return bool(result.rowcount)

    async def has_open_task(self, lead_id, kind: str) -> bool:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            found = await session.scalar(
                select(BydOperatorTask.id).where(
                    BydOperatorTask.tenant_id == TENANT,
                    BydOperatorTask.lead_id == int(lead_id),
                    BydOperatorTask.kind == kind,
                    BydOperatorTask.status == "open",
                )
            )
            return found is not None


_repository: Optional[BydRepository] = None


def get_repository() -> BydRepository:
    global _repository
    if _repository is None:
        _repository = BydRepository()
    return _repository
