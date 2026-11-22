"""Maskan — grave care. Mirrors `apps/maskan/models.py`.

Maskan is standalone: the catalogue, cemeteries, graves, orders and payments in
these tables are the real ones the agent sells from, not a cache of someone
else's system. Editing a price here changes what the bot quotes on its next
call; editing an *order* does not, because an order stores a snapshot of what
was sold (`items`).

Foreign keys are declared with `db_constraint=False`: the columns exist and
carry the right ids, but the panel must not depend on (or try to create) a
database-level constraint that Alembic did not put there.
"""

from __future__ import annotations

from django.db import models

from .base import CrmModel


class MaskanService(CrmModel):
    """The price list. `list_services` reads this on every quote."""

    id = models.AutoField(primary_key=True)
    code = models.CharField(max_length=40, unique=True)
    name_uz = models.CharField(max_length=160)
    name_ru = models.CharField(max_length=160, blank=True, default="")
    desc_uz = models.CharField(max_length=255, blank=True, default="")
    desc_ru = models.CharField(max_length=255, blank=True, default="")
    price = models.IntegerField(help_text="so‘m")
    sort = models.IntegerField(default=100)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "maskan_services"
        verbose_name = "service"
        verbose_name_plural = "services (price list)"
        ordering = ("sort", "code")

    def __str__(self) -> str:
        return f"{self.code} — {self.name_uz}"


class MaskanCemetery(CrmModel):
    """The service-area rule: a cemetery not in this table cannot be quoted for."""

    id = models.AutoField(primary_key=True)
    name_uz = models.CharField(max_length=160)
    name_ru = models.CharField(max_length=160, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    district = models.CharField(max_length=120, blank=True, default="")
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "maskan_cemeteries"
        verbose_name = "cemetery"
        verbose_name_plural = "cemeteries"
        ordering = ("city", "name_uz")

    def __str__(self) -> str:
        return self.name_uz


class MaskanGrave(CrmModel):
    """A grave a client registered, keyed by their Telegram chat id.

    There is no client account anywhere — the chat id *is* the identity.
    """

    id = models.AutoField(primary_key=True)
    chat_id = models.BigIntegerField()
    cemetery = models.ForeignKey(
        MaskanCemetery,
        on_delete=models.DO_NOTHING,
        db_column="cemetery_id",
        db_constraint=False,
        null=True,
        blank=True,
        related_name="graves",
    )
    cemetery_label = models.CharField(max_length=160, blank=True, default="")
    name = models.CharField(max_length=160)
    relation = models.CharField(max_length=60, blank=True, default="")
    born = models.IntegerField(null=True, blank=True)
    died = models.IntegerField(null=True, blank=True)
    sector = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "maskan_graves"
        verbose_name = "grave"
        verbose_name_plural = "graves"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.name} — {self.cemetery_label or self.cemetery_id or ''}".strip()


class MaskanPayment(CrmModel):
    """One invoice per order, amounts in **tiyin** (100 tiyin = 1 so‘m).

    Read-only in the panel. State here is asserted by the payment providers
    through `apps/maskan/payments_api.py`; hand-editing it would either thank a
    client who never paid or double-charge one who did.
    """

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.CharField(max_length=64)
    chat_id = models.BigIntegerField()
    lead_id = models.BigIntegerField(null=True, blank=True)
    order_id = models.BigIntegerField(null=True, blank=True)
    amount_tiyin = models.BigIntegerField()
    state = models.CharField(max_length=16)
    provider = models.CharField(max_length=16, null=True, blank=True)
    provider_txn_id = models.CharField(max_length=64, null=True, blank=True)
    create_time = models.BigIntegerField(default=0)
    perform_time = models.BigIntegerField(default=0)
    cancel_time = models.BigIntegerField(default=0)
    reason = models.IntegerField(null=True, blank=True)
    detail = models.JSONField(default=dict)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "maskan_payments"
        verbose_name = "payment"
        verbose_name_plural = "payments"
        ordering = ("-created_at",)

    @property
    def amount_sum(self) -> int:
        return self.amount_tiyin // 100

    def __str__(self) -> str:
        return f"#{self.id} {self.state} {self.amount_sum:,} so‘m"


class MaskanOrder(CrmModel):
    """A sold order. `items` is a snapshot — a later price edit cannot change it."""

    STATUS_CHOICES = [
        ("pending", "pending"),
        ("paid", "paid"),
        ("accepted", "accepted"),
        ("completed", "completed"),
        ("cancelled", "cancelled"),
    ]

    id = models.BigAutoField(primary_key=True)
    chat_id = models.BigIntegerField()
    grave = models.ForeignKey(
        MaskanGrave,
        on_delete=models.DO_NOTHING,
        db_column="grave_id",
        db_constraint=False,
        null=True,
        blank=True,
        related_name="orders",
    )
    grave_label = models.CharField(max_length=160, blank=True, default="")
    cemetery_label = models.CharField(max_length=160, blank=True, default="")
    items = models.JSONField(default=list)
    total = models.IntegerField(default=0, help_text="so‘m")
    frequency = models.CharField(max_length=16, default="once")
    status = models.CharField(max_length=16, default="pending", choices=STATUS_CHOICES)
    payment_id = models.BigIntegerField(null=True, blank=True)
    caretaker = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(CrmModel.Meta):
        db_table = "maskan_orders"
        verbose_name = "order"
        verbose_name_plural = "orders"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"#{self.id} {self.status} — {self.total:,} so‘m"


class MaskanLead(CrmModel):
    """Funnel state for one chat: stage, status, and what was learned about them."""

    STATUS_CHOICES = [
        ("active", "active"),
        ("won", "won"),
        ("lost", "lost"),
        ("paused", "paused"),
    ]

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.CharField(max_length=64, default="maskan")
    chat_id = models.BigIntegerField()
    current_stage = models.IntegerField(default=1)
    status = models.CharField(max_length=16, default="active", choices=STATUS_CHOICES)
    name = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=64, blank=True, default="")
    tg_username = models.CharField(max_length=255, blank=True, default="")
    request = models.TextField(blank=True, default="")
    # Legacy columns from the era when the Django backend owned the domain data.
    # Kept mapped so the panel shows the truth of the row, but no longer written.
    django_user_id = models.BigIntegerField(null=True, blank=True)
    django_grave_id = models.BigIntegerField(null=True, blank=True)
    django_order_id = models.BigIntegerField(null=True, blank=True)
    grave_label = models.CharField(max_length=255, blank=True, default="")
    cemetery_label = models.CharField(max_length=255, blank=True, default="")
    order_total = models.BigIntegerField(null=True, blank=True)
    order_frequency = models.CharField(max_length=16, default="once")
    service_codes = models.JSONField(default=list)
    last_order_status = models.CharField(max_length=24, blank=True, default="")
    last_payment_status = models.CharField(max_length=24, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=255, null=True, blank=True)
    stage_entered_at = models.DateTimeField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "maskan_leads"
        verbose_name = "lead"
        verbose_name_plural = "leads (funnel)"
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return f"#{self.id} {self.name or self.chat_id} — stage {self.current_stage}"


class MaskanScheduledTask(CrmModel):
    """A row of the durable touch queue: run `action_type` for a lead at a time."""

    STATUS_CHOICES = [
        ("pending", "pending"),
        ("running", "running"),
        ("done", "done"),
        ("cancelled", "cancelled"),
        ("failed", "failed"),
    ]

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.CharField(max_length=64, default="maskan")
    lead = models.ForeignKey(
        MaskanLead,
        on_delete=models.DO_NOTHING,
        db_column="lead_id",
        db_constraint=False,
        related_name="tasks",
    )
    chat_id = models.BigIntegerField()
    action_type = models.CharField(max_length=64)
    stage = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=16, default="pending", choices=STATUS_CHOICES)
    attempts = models.IntegerField(default=0)
    dedup_key = models.CharField(max_length=255)
    last_error = models.TextField(null=True, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "maskan_scheduled_tasks"
        verbose_name = "scheduled task"
        verbose_name_plural = "scheduled tasks"
        ordering = ("scheduled_for",)

    def __str__(self) -> str:
        return f"{self.action_type} @ {self.scheduled_for:%Y-%m-%d %H:%M}"
