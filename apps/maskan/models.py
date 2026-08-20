"""Data models for the Maskan funnel (our own, Postgres-backed).

Deliberately **thin**: the Maskan Django backend owns the catalogue, the graves,
the orders and the payments, and this tenant reaches them over HTTP. What lives
here is only the state the backend has no concept of — how far a *conversation*
has travelled toward an order, and the queue of time-delayed touches that pushes
it along:

* ``maskan_leads``           — one client conversation as a deal: which stage it
                               is in, the Django ids we have resolved for it
                               (user / grave / order), and the last order status
                               we told the client about (the watcher's anchor).
* ``maskan_scheduled_tasks`` — "run action A for lead L at time T". Every
                               follow-up, payment reminder, SLA check, seasonal
                               memorial nudge and repeat-care offer is a row
                               here; the scheduler fires them. Idempotent
                               enqueue via ``dedup_key``; concurrency-safe claim
                               via FOR UPDATE SKIP LOCKED.

There is no service/price table on purpose — duplicating the Django catalogue
would create two prices for one service, and only one of them would be the one
the customer is actually charged.

Keep this module import-light (stdlib + sqlalchemy + ``db``): the repository —
and through it the admin panel — imports it and must not pull in the
agent/Telegram runtime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base

# ----- funnel stages --------------------------------------------------------
# The path a Maskan client actually walks: they arrive, we learn whose grave and
# where, we quote the care, they pay, a caretaker does the work, we send the
# before/after photos — and then it repeats, because grave care is seasonal.
STAGE_NEW = 1        # Yangi murojaat — hali qabr ma'lumoti yo'q
STAGE_GRAVE = 2      # Qabr aniqlandi va ro'yxatga olindi
STAGE_QUOTED = 3     # Xizmatlar tanlandi, narx aytildi
STAGE_PAYMENT = 4    # Payme havolasi yuborildi, to'lov kutilmoqda
STAGE_ORDERED = 5    # To'landi — buyurtma go'rkovga uzatildi
STAGE_PROGRESS = 6   # Go'rkov qabul qildi, ish ketmoqda
STAGE_DONE = 7       # Bajarildi — oldin/keyin rasmlar mijozga yuborildi
STAGE_REPEAT = 8     # Takroriy parvarish kutilmoqda (obuna / mavsumiy)

STAGE_TITLES_UZ = {
    STAGE_NEW: "Yangi murojaat",
    STAGE_GRAVE: "Qabr aniqlandi",
    STAGE_QUOTED: "Narx aytildi",
    STAGE_PAYMENT: "To'lov kutilmoqda",
    STAGE_ORDERED: "Buyurtma berildi",
    STAGE_PROGRESS: "Ish jarayonda",
    STAGE_DONE: "Bajarildi",
    STAGE_REPEAT: "Takroriy parvarish",
}

# ----- lead status (orthogonal to stage) ------------------------------------
# active — in the funnel, the scheduler may fire touches for it
# cold   — follow-ups exhausted with no reply; only seasonal touches remain
# closed — terminal (won and finished, or lost); pending touches are purged
STATUS_ACTIVE = "active"
STATUS_COLD = "cold"
STATUS_CLOSED = "closed"

# `close_reason` marker for a client who asked us to stop writing. The lead is
# closed with this reason so no outreach ever fires again; the AI still answers
# if the client writes first.
DO_NOT_CONTACT_REASON = "Mijoz yozmaslikni so'radi"

# ----- scheduled-task status ------------------------------------------------
TASK_PENDING = "pending"
TASK_RUNNING = "running"      # claimed by a poller, in flight
TASK_DONE = "done"
TASK_CANCELLED = "cancelled"
TASK_FAILED = "failed"


class MaskanLead(Base):
    """One client conversation progressing toward (and past) an order.

    The `django_*` columns are our handles into the backend — resolved once and
    reused, so a returning client doesn't get re-onboarded. `last_order_status`
    is what makes the watcher idempotent: it stores the status we have already
    reacted to, so a status the client was already told about never produces a
    second message.
    """

    __tablename__ = "maskan_leads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="maskan", index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)

    current_stage: Mapped[int] = mapped_column(Integer, default=STAGE_NEW, index=True)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_ACTIVE, index=True)

    # Who we're talking to (Telegram side)
    name: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    tg_username: Mapped[str] = mapped_column(String(255), default="")

    # What they asked for, in their own words — the operator's context on handoff
    request: Mapped[str] = mapped_column(Text, default="")

    # Resolved Django handles
    django_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    django_grave_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    django_order_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, index=True
    )

    # Denormalised copies for operator notifications + the admin panel, so
    # neither has to call the backend to render a readable line.
    grave_label: Mapped[str] = mapped_column(String(255), default="")
    cemetery_label: Mapped[str] = mapped_column(String(255), default="")

    # Order snapshot — the quote we gave and how the backend last reported it.
    order_total: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    order_frequency: Mapped[str] = mapped_column(String(16), default="once")
    # Service codes in the current quote/order (["clean", "weed", …]).
    service_codes: Mapped[list] = mapped_column(JSONB, default=list)
    # The backend's `status` / `payment_status` we have already acted on.
    last_order_status: Mapped[str] = mapped_column(String(24), default="")
    last_payment_status: Mapped[str] = mapped_column(String(24), default="")
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    close_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    stage_entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "stage": self.current_stage,
            "stage_title": STAGE_TITLES_UZ.get(self.current_stage, ""),
            "status": self.status,
            "name": self.name,
            "phone": self.phone,
            "request": self.request,
            "grave": self.grave_label,
            "cemetery": self.cemetery_label,
            "django_user_id": self.django_user_id,
            "django_grave_id": self.django_grave_id,
            "django_order_id": self.django_order_id,
            "order_total": self.order_total,
            "order_frequency": self.order_frequency,
            "services": list(self.service_codes or []),
            "order_status": self.last_order_status,
            "payment_status": self.last_payment_status,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "close_reason": self.close_reason,
        }


class MaskanScheduledTask(Base):
    """A single time-delayed funnel action: "run `action_type` for `lead_id` at
    `scheduled_for`".

    `chat_id` is denormalised so an executor sends without a join. `dedup_key`
    makes enqueue idempotent (a repeated stage transition can't double-schedule
    a reminder chain). `status` + `attempts` give restart-safe, retryable
    execution; a poller claims due rows with FOR UPDATE SKIP LOCKED so two
    processes never fire the same task.
    """

    __tablename__ = "maskan_scheduled_tasks"
    # The poller's hot query: pending rows whose time has come, oldest first.
    __table_args__ = (Index("ix_maskan_scheduled_tasks_due", "status", "scheduled_for"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="maskan", index=True)
    lead_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)

    action_type: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default=TASK_PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    # Deterministic key — unique per (lead, stage, action, sequence) so
    # re-enqueueing a stage's plan is a no-op / a timer reset.
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True)

    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
