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

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text
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
# ----- payment (our own merchant account) -----------------------------------
# The bot issues its own checkout links (Payme + Uzum) against the operator's
# merchant account, so one row per order is our invoice. States follow Payme's
# transaction lifecycle, which Uzum's check/create/confirm/reverse maps onto
# cleanly enough to share.
# ----- own catalogue + orders (standalone mode) -----------------------------
# The tenant used to read the catalogue, graves and orders out of the Maskan
# Django backend. It now owns them: prices, cemeteries, graves and orders all
# live here, so the bot sells and takes money without the backend being up.
ORDER_PENDING = "pending"      # created, awaiting payment
ORDER_PAID = "paid"            # money received, waiting for a caretaker
ORDER_ACCEPTED = "accepted"    # caretaker took the job
ORDER_COMPLETED = "completed"  # work done
ORDER_CANCELLED = "cancelled"

ORDER_STATUS_UZ = {
    ORDER_PENDING: "To'lov kutilmoqda",
    ORDER_PAID: "To'landi — xodimga uzatildi",
    ORDER_ACCEPTED: "Xodim qabul qildi, ish ketmoqda",
    ORDER_COMPLETED: "Bajarildi",
    ORDER_CANCELLED: "Bekor qilindi",
}

PAYMENT_NEW = "new"            # invoice created, no provider transaction yet
PAYMENT_CREATED = "created"    # provider opened a transaction, money not taken
PAYMENT_PAID = "paid"          # money received
PAYMENT_CANCELLED = "cancelled"

PROVIDER_PAYME = "payme"
PROVIDER_UZUM = "uzum"

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


class MaskanPayment(Base):
    """One invoice = one order the client was asked to pay for.

    The bot builds its own Payme/Uzum checkout links against the operator's
    merchant account, so *this* table — not the Django backend — is what the
    payment providers talk about: the `account` value in a Payme checkout link
    is this row's `id`, and Payme's Merchant API (CheckPerformTransaction →
    CreateTransaction → PerformTransaction) drives its state.

    Both providers write here and the first one to pay wins (`provider` records
    which). `notified_at` is the handoff to the bot process: the webhook service
    only marks the row paid, and `payment_watcher.py` — which lives in the bot
    process and can actually reach Telegram — picks up unnotified paid rows,
    tells the client, notifies the operators and advances the funnel. That split
    is deliberate: money is recorded by whoever hears about it first, and
    messaging stays where the Telethon client is.

    Amounts are in **tiyin** (1 so'm = 100 tiyin) because that is the unit both
    providers speak; `amount_som` is only for display.
    """

    __tablename__ = "maskan_payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="maskan", index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    lead_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # The Django backend's order id, when the order was created there.
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)

    amount_tiyin: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(16), default=PAYMENT_NEW, index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # Provider transaction identity + Payme's millisecond timestamps, which the
    # Merchant API must echo back verbatim in CheckTransaction.
    provider_txn_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    create_time: Mapped[int] = mapped_column(BigInteger, default=0)
    perform_time: Mapped[int] = mapped_column(BigInteger, default=0)
    cancel_time: Mapped[int] = mapped_column(BigInteger, default=0)
    reason: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # What was bought — service codes/labels, so an operator reading the alert
    # knows what to dispatch without another lookup.
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)

    notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_maskan_payments_state", "state", "notified_at"),
    )


class MaskanService(Base):
    """The price list the agent quotes from — this tenant's own catalogue.

    `list_services` reads it on every quote, so an admin price change lands in
    the next sentence the agent says. `code` is the stable handle an order's
    items refer to; `price` is in **so'm** (the unit the client hears), and only
    the payment layer converts to tiyin.
    """

    __tablename__ = "maskan_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name_uz: Mapped[str] = mapped_column(String(160))
    name_ru: Mapped[str] = mapped_column(String(160), default="")
    desc_uz: Mapped[str] = mapped_column(String(255), default="")
    desc_ru: Mapped[str] = mapped_column(String(255), default="")
    price: Mapped[int] = mapped_column(Integer)
    sort: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaskanCemetery(Base):
    """Cemeteries the caretakers actually cover.

    Being our own table *is* the service-area rule: a cemetery outside Tashkent
    city/region simply isn't here, so the agent cannot quote for it. Search is a
    plain ILIKE over name/city/district — clients half-remember names.
    """

    __tablename__ = "maskan_cemeteries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name_uz: Mapped[str] = mapped_column(String(160), index=True)
    name_ru: Mapped[str] = mapped_column(String(160), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    district: Mapped[str] = mapped_column(String(120), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaskanGrave(Base):
    """A deceased relative registered by a client, keyed by their Telegram chat.

    No account, no password, no app: the chat id *is* the identity. That is the
    whole point of standalone mode — a grieving client should not have to
    register anywhere before someone can look after a grave.
    """

    __tablename__ = "maskan_graves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    cemetery_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cemetery_label: Mapped[str] = mapped_column(String(160), default="")
    name: Mapped[str] = mapped_column(String(160))
    relation: Mapped[str] = mapped_column(String(60), default="")
    born: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    died: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sector: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaskanOrder(Base):
    """An order the bot created and takes money for itself.

    `items` is a **snapshot** of what was sold (code, name, price at the time),
    not a join: a later price edit must never change what a client already
    agreed to pay. `payment_id` points at the `maskan_payments` invoice whose
    Payme/Uzum callback flips this row to paid; the caretaker steps after that
    are set by staff from the operator bot.
    """

    __tablename__ = "maskan_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    grave_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grave_label: Mapped[str] = mapped_column(String(160), default="")
    cemetery_label: Mapped[str] = mapped_column(String(160), default="")
    items: Mapped[list] = mapped_column(JSONB, default=list)
    total: Mapped[int] = mapped_column(Integer, default=0)
    frequency: Mapped[str] = mapped_column(String(16), default="once")
    status: Mapped[str] = mapped_column(String(16), default=ORDER_PENDING, index=True)
    payment_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    caretaker: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
