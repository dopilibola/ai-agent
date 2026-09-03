"""Admin for the Maskan tenant.

One thing to keep in mind while editing here: the funnel transitions
(`apps/maskan/funnel.py::on_*`) run in the bot process, not in this panel.
Changing an order's status from the admin updates the row but does **not** fire
`on_payment_received` / `on_order_accepted` / `on_order_completed` — so it will
not message the client or re-plan their scheduled touches. Move an order
through the operator bot when the client should hear about it; use the panel to
fix data, not to drive the funnel.
"""

from __future__ import annotations

from django.contrib import admin

from crm.models import (
    MaskanCemetery,
    MaskanGrave,
    MaskanLead,
    MaskanOrder,
    MaskanPayment,
    MaskanScheduledTask,
    MaskanService,
)
from panel.sites import thread_link, user_link

from .base import CrmAdmin, ReadOnlyAdmin, money, pretty_json


@admin.register(MaskanService)
class MaskanServiceAdmin(CrmAdmin):
    """The live price list — `list_services` reads it on every quote.

    An edit here changes what the agent quotes from its next call onward. It
    does not change what an already-placed order costs: an order stores a
    snapshot of the items as sold.
    """

    list_display = ("code", "name_uz", "name_ru", "price_display", "sort", "active")
    list_filter = ("active",)
    search_fields = ("code", "name_uz", "name_ru", "desc_uz", "desc_ru")
    list_editable = ("sort", "active")
    ordering = ("sort", "code")

    @admin.display(description="narx", ordering="price")
    def price_display(self, obj):
        return money(obj.price)


@admin.register(MaskanCemetery)
class MaskanCemeteryAdmin(CrmAdmin):
    """The service area. A cemetery missing here is quoted as `out_of_area`."""

    list_display = ("name_uz", "name_ru", "city", "district", "active", "grave_count")
    list_filter = ("active", "city")
    search_fields = ("name_uz", "name_ru", "city", "district")
    list_editable = ("active",)

    @admin.display(description="qabrlar")
    def grave_count(self, obj):
        return obj.graves.count()


@admin.register(MaskanGrave)
class MaskanGraveAdmin(CrmAdmin):
    list_display = (
        "id",
        "name",
        "relation",
        "cemetery_label",
        "sector",
        "born",
        "died",
        "chat_id",
        "created_at",
    )
    list_filter = ("cemetery",)
    search_fields = ("name", "cemetery_label", "sector", "chat_id")
    date_hierarchy = "created_at"
    autocomplete_fields = ()
    raw_id_fields = ("cemetery",)


@admin.register(MaskanOrder)
class MaskanOrderAdmin(CrmAdmin):
    list_display = (
        "id",
        "created_at",
        "status",
        "total_display",
        "frequency",
        "grave_label",
        "cemetery_label",
        "caretaker",
        "chat_id",
    )
    list_filter = ("status", "frequency")
    search_fields = ("grave_label", "cemetery_label", "caretaker", "chat_id", "id")
    date_hierarchy = "created_at"
    readonly_fields = (
        "id",
        "chat_id",
        "items_pretty",
        "created_at",
        "updated_at",
        "paid_at",
        "accepted_at",
        "completed_at",
        "payment_id",
    )
    exclude = ("items",)
    raw_id_fields = ("grave",)

    @admin.display(description="summa", ordering="total")
    def total_display(self, obj):
        return money(obj.total)

    @admin.display(description="tarkibi (sotilgan paytdagi narxlar)")
    def items_pretty(self, obj):
        return pretty_json(obj.items)


@admin.register(MaskanPayment)
class MaskanPaymentAdmin(ReadOnlyAdmin):
    """Read-only: payment state is asserted by Payme/Uzum, never by a person."""

    list_display = (
        "id",
        "created_at",
        "state",
        "provider",
        "amount_display",
        "chat_id",
        "order_id",
        "notified_at",
    )
    list_filter = ("state", "provider", "tenant_id")
    search_fields = ("id", "chat_id", "order_id", "provider_txn_id")
    date_hierarchy = "created_at"
    readonly_fields = ("detail_pretty",)
    exclude = ("detail",)

    @admin.display(description="summa", ordering="amount_tiyin")
    def amount_display(self, obj):
        return money(obj.amount_tiyin, divisor=100)

    @admin.display(description="detail")
    def detail_pretty(self, obj):
        return pretty_json(obj.detail)


@admin.register(MaskanLead)
class MaskanLeadAdmin(CrmAdmin):
    list_display = (
        "id",
        "name",
        "phone",
        "current_stage",
        "status",
        "cemetery_label",
        "order_total_display",
        "last_payment_status",
        "updated_at",
        "who",
        "transcript",
    )
    list_filter = ("status", "current_stage", "order_frequency", "last_order_status")
    search_fields = ("name", "phone", "tg_username", "chat_id", "request")
    date_hierarchy = "created_at"
    readonly_fields = (
        "id",
        "chat_id",
        "tenant_id",
        "stage_entered_at",
        "created_at",
        "updated_at",
        "paid_at",
        "completed_at",
        "service_codes_pretty",
        "transcript",
        "django_user_id",
        "django_grave_id",
        "django_order_id",
    )
    exclude = ("service_codes",)

    @admin.display(description="summa", ordering="order_total")
    def order_total_display(self, obj):
        return money(obj.order_total)

    @admin.display(description="xizmatlar")
    def service_codes_pretty(self, obj):
        return pretty_json(obj.service_codes)

    @admin.display(description="suhbat")
    def transcript(self, obj):
        return thread_link(f"maskan:customer:{obj.chat_id}", "ochish")

    @admin.display(description="chat", ordering="chat_id")
    def who(self, obj):
        return user_link(obj.tenant_id, obj.chat_id, obj.name or obj.chat_id)


@admin.register(MaskanScheduledTask)
class MaskanScheduledTaskAdmin(CrmAdmin):
    """The durable touch queue.

    Cancelling a pending row here stops that one touch. It does not stop the
    funnel from re-enqueueing it on the next stage transition — enqueue is
    idempotent by `dedup_key` and deliberately *resets* an existing row.
    """

    list_display = (
        "id",
        "scheduled_for",
        "status",
        "action_type",
        "stage",
        "attempts",
        "lead_id",
        "chat_id",
        "executed_at",
    )
    list_filter = ("status", "action_type", "stage")
    search_fields = ("dedup_key", "chat_id", "lead__id", "last_error")
    date_hierarchy = "scheduled_for"
    readonly_fields = (
        "id",
        "tenant_id",
        "chat_id",
        "dedup_key",
        "created_at",
        "executed_at",
        "attempts",
        "payload_pretty",
    )
    exclude = ("payload",)
    raw_id_fields = ("lead",)
    actions = ("cancel_tasks",)

    @admin.display(description="payload")
    def payload_pretty(self, obj):
        return pretty_json(obj.payload)

    @admin.action(description="Bekor qilish (cancelled)")
    def cancel_tasks(self, request, queryset):
        n = queryset.filter(status__in=("pending", "failed")).update(status="cancelled")
        self.message_user(request, f"{n} ta vazifa bekor qilindi.")
