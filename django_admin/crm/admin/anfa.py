"""Admin for the Anfa tenant (Tashkent clinic).

Both tables are reconciled whole from the clinic's exports (`.xlsx` service
list, `.docx` doctor roster). An edit made here is therefore **temporary**: the
next import overwrites any row whose content hash changed upstream. Fix the
source file when a change should stick; edit here only to take something out of
circulation quickly (`active = False`).
"""

from __future__ import annotations

from django.contrib import admin

from crm.models import AnfaCatalogItem, AnfaDoctor

from .base import CrmAdmin, money, pretty_json


@admin.register(AnfaCatalogItem)
class AnfaCatalogItemAdmin(CrmAdmin):
    list_display = ("title", "tab", "category", "price_display", "active", "updated_at")
    list_filter = ("tab", "active", "category")
    search_fields = ("title", "category")
    list_editable = ("active",)
    readonly_fields = ("id", "content_hash", "created_at", "updated_at")

    @admin.display(description="narx", ordering="price")
    def price_display(self, obj):
        return f"{obj.price:,} {obj.currency}".replace(",", " ")


@admin.register(AnfaDoctor)
class AnfaDoctorAdmin(CrmAdmin):
    """`hours_label` is a **walk-in** time — the clinic has no online booking."""

    list_display = ("fullname", "speciality", "hours_label", "active", "updated_at")
    list_filter = ("active", "speciality")
    search_fields = ("fullname", "speciality", "experience")
    list_editable = ("active",)
    readonly_fields = ("id", "content_hash", "created_at", "updated_at", "schedule_pretty")
    exclude = ("schedule",)

    @admin.display(description="jadval")
    def schedule_pretty(self, obj):
        return pretty_json(obj.schedule)
