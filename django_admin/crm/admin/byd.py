"""Admin for the BYD Medical tenant (detox clinic).

Same caveat as Maskan: the funnel transitions run in the bot process. Editing a
lead's stage here moves the row, not the customer — no touch is sent, no
scheduled plan is re-enqueued, and Bitrix is not told. The `bitrix_*` columns
are the link back to the deal and are read-only for that reason.
"""

from __future__ import annotations

from django.contrib import admin

from crm.models import BydLead, BydOperatorTask, BydProgram, BydScheduledTask
from panel.sites import thread_link, user_link

from .base import CrmAdmin, money, pretty_json


@admin.register(BydProgram)
class BydProgramAdmin(CrmAdmin):
    list_display = ("code", "title", "days", "price_display", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("code", "title")
    list_editable = ("active",)

    @admin.display(description="narx", ordering="price")
    def price_display(self, obj):
        return money(obj.price)


@admin.register(BydLead)
class BydLeadAdmin(CrmAdmin):
    list_display = (
        "id",
        "name",
        "phone",
        "city",
        "current_stage",
        "status",
        "program",
        "arrival_date",
        "updated_at",
        "who",
        "transcript",
    )
    list_filter = ("status", "current_stage", "city", "last_call_outcome", "program")
    search_fields = ("name", "phone", "tg_username", "chat_id", "request")
    date_hierarchy = "created_at"
    raw_id_fields = ("program",)
    readonly_fields = (
        "id",
        "tenant_id",
        "chat_id",
        "stage_entered_at",
        "created_at",
        "updated_at",
        "operator_first_action_at",
        "prepayment_received_at",
        "voucher_number",
        "bitrix_contact_id",
        "bitrix_deal_id",
        "bitrix_stage_id",
        "transcript",
    )

    @admin.display(description="suhbat")
    def transcript(self, obj):
        return thread_link(f"byd:customer:{obj.chat_id}", "ochish")

    @admin.display(description="chat", ordering="chat_id")
    def who(self, obj):
        return user_link(obj.tenant_id, obj.chat_id, obj.name or obj.chat_id)


@admin.register(BydScheduledTask)
class BydScheduledTaskAdmin(CrmAdmin):
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
    search_fields = ("dedup_key", "chat_id", "last_error")
    date_hierarchy = "scheduled_for"
    raw_id_fields = ("lead",)
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
    actions = ("cancel_tasks",)

    @admin.display(description="payload")
    def payload_pretty(self, obj):
        return pretty_json(obj.payload)

    @admin.action(description="Bekor qilish (cancelled)")
    def cancel_tasks(self, request, queryset):
        n = queryset.filter(status__in=("pending", "failed")).update(status="cancelled")
        self.message_user(request, f"{n} ta vazifa bekor qilindi.")


@admin.register(BydOperatorTask)
class BydOperatorTaskAdmin(CrmAdmin):
    list_display = ("id", "created_at", "kind", "status", "title", "due_at", "lead_id", "done_at")
    list_filter = ("status", "kind")
    search_fields = ("title", "chat_id")
    date_hierarchy = "created_at"
    raw_id_fields = ("lead",)
    readonly_fields = ("id", "tenant_id", "chat_id", "created_at", "bitrix_task_id", "checklist_pretty")
    exclude = ("checklist",)

    @admin.display(description="checklist")
    def checklist_pretty(self, obj):
        return pretty_json(obj.checklist)
