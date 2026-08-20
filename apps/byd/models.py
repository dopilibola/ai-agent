"""Data models for the BYD Medical funnel (our own, Postgres-backed).

The BYD Bitrix24 funnel is rebuilt here as four tables on the shared
`db.models.Base`:

* ``byd_leads``            — a deal moving through the 8 funnel stages, plus the
                             fields the funnel collects (name/request/city,
                             program, arrival date, DOB, prepayment).
* ``byd_scheduled_tasks``  — the keystone: a durable queue of "run action A for
                             lead L at time T" rows. Every one of the funnel's
                             time-delayed touches (+2h, +24h, arrival−3d, +60d,
                             birthday, …) is a row here; a polling SyncJob fires
                             them. Idempotent enqueue via ``dedup_key``;
                             concurrency-safe claim via FOR UPDATE SKIP LOCKED.
* ``byd_programs``         — the 7/14/21-day program price list (the 10%
                             prepayment + remaining balance derive from it).
* ``byd_operator_tasks``   — operator to-dos with state (call-now SLA, check
                             payment, prepare, confirm) + the Stage-3 SPIN
                             checklist — Bitrix "tasks" without Bitrix.

Keep this module import-light (stdlib + sqlalchemy + ``db``): the repository and,
in turn, the admin panel import it and must not pull in the agent/Telegram
runtime.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base

# ----- funnel stages (the 8 Bitrix stages, 1-indexed per the spec) ----------
STAGE_NEW = 1            # Новая заявка
STAGE_NO_ANSWER = 2      # Не дозвон (5-touch drip)
STAGE_NEGOTIATION = 3    # Переговоры (operator-driven, SPIN)
STAGE_CONSULT = 4        # Консультация назначена
STAGE_PREPAYMENT = 5     # Запрос предоплаты
STAGE_BOOKED = 6         # Бронь (paid)
STAGE_CONFIRMED = 7      # Подтверждение брони
STAGE_DONE = 8           # Успешно реализовано

STAGE_TITLES_RU = {
    STAGE_NEW: "Новая заявка",
    STAGE_NO_ANSWER: "Не дозвон",
    STAGE_NEGOTIATION: "Переговоры",
    STAGE_CONSULT: "Консультация назначена",
    STAGE_PREPAYMENT: "Запрос предоплаты",
    STAGE_BOOKED: "Бронь",
    STAGE_CONFIRMED: "Подтверждение брони",
    STAGE_DONE: "Успешно реализовано",
}

# ----- lead status (orthogonal to stage) ------------------------------------
# active  — in the funnel, the scheduler may fire tasks for it
# cold    — 5 touches exhausted with no reply; one +30d reactivation pending
# closed  — terminal (lost or won); active-lead lookups skip it and all pending
#           scheduled tasks are purged on entry
STATUS_ACTIVE = "active"
STATUS_COLD = "cold"
STATUS_CLOSED = "closed"

# `close_reason` marker for a client who asked us to stop writing (script:
# «больше не пишите» / удаление данных). The lead is closed with this reason so
# no outreach ever fires again; the AI still answers if the client writes first.
DO_NOT_CONTACT_REASON = "Не беспокоить: просьба клиента"

# ----- scheduled-task status ------------------------------------------------
TASK_PENDING = "pending"
TASK_RUNNING = "running"      # claimed by a poller, in flight
TASK_DONE = "done"
TASK_CANCELLED = "cancelled"
TASK_FAILED = "failed"


class BydLead(Base):
    """One deal/lead progressing through the BYD sales funnel.

    One active lead per customer chat. `current_stage` is the Bitrix stage;
    `stage_entered_at` anchors the 3h-no-action escalation and audit. Funnel
    actions are NOT stored here — they live as `byd_scheduled_tasks` rows keyed
    off this lead.
    """

    __tablename__ = "byd_leads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="byd", index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)

    current_stage: Mapped[int] = mapped_column(Integer, default=STAGE_NEW, index=True)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_ACTIVE, index=True)

    # Stage 1 capture
    name: Mapped[str] = mapped_column(String(255), default="")
    request: Mapped[str] = mapped_column(Text, default="")     # что беспокоит
    city: Mapped[str] = mapped_column(String(128), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    tg_username: Mapped[str] = mapped_column(String(255), default="")

    # Collected during negotiation / booking
    program_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # "7"/"14"/"21"
    arrival_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Money (UZS sum)
    total_amount: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    prepayment_amount: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    prepayment_received_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voucher_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Operator activity — drives the Stage-1 SLA logic (10-min reminder cancel +
    # 3h manager escalation): set the first time an operator records a call
    # outcome or otherwise acts on the lead.
    operator_first_action_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_call_outcome: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    close_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Bitrix24 mirror refs. `bitrix_stage_id` is the deal's STAGE_ID as we last
    # synced it (pushed or pulled) — the echo-suppression anchor: the pull job
    # treats a differing live STAGE_ID as an operator move made in Bitrix, and
    # the push skips the stage write when it already matches.
    bitrix_contact_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    bitrix_deal_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    bitrix_stage_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    stage_entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def remaining_amount(self) -> Optional[int]:
        if self.total_amount is None:
            return None
        return self.total_amount - (self.prepayment_amount or 0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "stage": self.current_stage,
            "stage_title": STAGE_TITLES_RU.get(self.current_stage, ""),
            "status": self.status,
            "name": self.name,
            "request": self.request,
            "city": self.city,
            "phone": self.phone,
            "program_code": self.program_code,
            "arrival_date": self.arrival_date.isoformat() if self.arrival_date else None,
            "date_of_birth": self.date_of_birth.isoformat() if self.date_of_birth else None,
            "total_amount": self.total_amount,
            "prepayment_amount": self.prepayment_amount,
            "prepayment_received_at": (
                self.prepayment_received_at.isoformat()
                if self.prepayment_received_at
                else None
            ),
            "voucher_number": self.voucher_number,
            "bitrix_deal_id": self.bitrix_deal_id,
            "last_call_outcome": self.last_call_outcome,
            "close_reason": self.close_reason,
        }


class BydScheduledTask(Base):
    """A single time-delayed funnel action: "run `action_type` for `lead_id` at
    `scheduled_for`".

    `chat_id` is denormalized so the executor sends without a join. `dedup_key`
    makes enqueue idempotent (a re-run of the same stage transition can't
    double-schedule a drip). `status` + `attempts` give restart-safe,
    retryable execution; a poller claims `pending` rows whose time has come with
    FOR UPDATE SKIP LOCKED so two processes never fire the same task.
    """

    __tablename__ = "byd_scheduled_tasks"
    # The poller's hot query: pending rows whose time has come, oldest first.
    __table_args__ = (Index("ix_byd_scheduled_tasks_due", "status", "scheduled_for"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="byd", index=True)
    lead_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)

    action_type: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default=TASK_PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    # Deterministic key — unique per (tenant, lead, stage, action, sequence) so
    # re-enqueueing a stage's plan is a no-op (ON CONFLICT DO NOTHING).
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True)

    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BydProgram(Base):
    """A detox program (7 / 14 / 21 day) and its full price (UZS sum).

    The 10% prepayment and the voucher's remaining balance derive from `price`.
    """

    __tablename__ = "byd_programs"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)  # "7"/"14"/"21"
    title: Mapped[str] = mapped_column(String(255))
    days: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[int] = mapped_column(BigInteger, default=0)  # full price, UZS sum
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "days": self.days,
            "price": self.price,
            "active": self.active,
        }


class BydOperatorTask(Base):
    """An operator to-do with state — Bitrix "tasks" without Bitrix.

    `kind` is the funnel task type (call_now / check_payment / prepare /
    confirm). `checklist` carries the Stage-3 SPIN steps. `status` is flipped to
    done by an inline "✅ Выполнено" button. The 3h escalation and the 10-min
    reminder both key off the open/closed state of the call_now task.
    """

    __tablename__ = "byd_operator_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="byd", index=True)
    lead_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)

    kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open/done
    checklist: Mapped[list] = mapped_column(JSONB, default=list)

    # Mirrored Bitrix24 task id (tasks.task.add) — closed there when closed here.
    bitrix_task_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "chat_id": self.chat_id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "checklist": list(self.checklist or []),
        }
