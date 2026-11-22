"""Admin for the Oygul tenant (flower shop)."""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from crm.models import OygulBouquet, OygulOrder
from panel.sites import thread_link

from .base import CrmAdmin, money, pretty_json


@admin.register(OygulBouquet)
class OygulBouquetAdmin(CrmAdmin):
    """The catalogue.

    Deactivating a bouquet here hides it from the shop's own listings, but the
    agent finds bouquets through **Chroma**, not this table — the CLIP vector is
    written by `BouquetSearch.index` when the merchant bot saves one. Removing a
    row without re-indexing leaves a vector pointing at nothing, so prefer
    `active = False` to deletion.
    """

    list_display = ("thumb", "id", "name", "price_display", "branch_id", "active", "updated_at")
    list_display_links = ("id", "name")
    list_filter = ("active", "branch_id")
    search_fields = ("id", "name", "description")
    list_editable = ("active",)
    readonly_fields = ("id", "created_at", "updated_at", "preview", "tags_pretty", "products_pretty")
    exclude = ("tags", "products_spent")

    @admin.display(description="narx", ordering="price")
    def price_display(self, obj):
        return money(obj.price, divisor=100)

    @admin.display(description="")
    def thumb(self, obj):
        if not obj.photo_url:
            return "—"
        return format_html(
            '<img src="{}" style="height:44px;border-radius:4px" loading="lazy">',
            obj.photo_url,
        )

    @admin.display(description="rasm")
    def preview(self, obj):
        if not obj.photo_url:
            return "—"
        return format_html(
            '<a href="{0}" target="_blank"><img src="{0}" style="max-height:320px"></a>',
            obj.photo_url,
        )

    @admin.display(description="teglar")
    def tags_pretty(self, obj):
        return pretty_json(obj.tags)

    @admin.display(description="sarflangan mahsulotlar")
    def products_pretty(self, obj):
        return pretty_json(obj.products_spent)


@admin.register(OygulOrder)
class OygulOrderAdmin(CrmAdmin):
    list_display = (
        "id",
        "created_at",
        "status",
        "bouquet_name",
        "total_display",
        "recipient_name",
        "recipient_phone",
        "delivery_time",
        "transcript",
    )
    list_filter = ("status", "is_surprise")
    search_fields = (
        "bouquet_name",
        "customer_name",
        "customer_username",
        "recipient_name",
        "recipient_phone",
        "address",
        "chat_id",
    )
    date_hierarchy = "created_at"
    readonly_fields = ("id", "chat_id", "created_at", "updated_at", "preview", "transcript")

    @admin.display(description="jami", ordering="bouquet_price_sum")
    def total_display(self, obj):
        return money(obj.total_sum)

    @admin.display(description="rasm")
    def preview(self, obj):
        if not obj.bouquet_photo_url:
            return "—"
        return format_html(
            '<a href="{0}" target="_blank"><img src="{0}" style="max-height:320px"></a>',
            obj.bouquet_photo_url,
        )

    @admin.display(description="suhbat")
    def transcript(self, obj):
        return thread_link(f"oygul:customer:{obj.chat_id}", "ochish")
