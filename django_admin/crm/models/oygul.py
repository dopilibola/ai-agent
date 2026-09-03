"""Oygul — flower shop. Mirrors `apps/oygul/models.py`.

Note the units: bouquet prices are stored in **tiyin** (`price // 100` = so‘m)
because they came in from the merchant bot that way, while order amounts are
already in so‘m (`*_sum`). The admin surfaces both as so‘m so nobody has to
remember which is which.
"""

from __future__ import annotations

from django.db import models

from .base import CrmModel


class OygulBouquet(CrmModel):
    """A catalogue item. The id is the Chroma document id — same string, so a
    row here and its CLIP vector stay findable from each other."""

    id = models.CharField(max_length=64, primary_key=True)
    branch_id = models.CharField(max_length=64, blank=True, default="")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list)
    products_spent = models.JSONField(default=list)
    photo_url = models.TextField(blank=True, default="")
    price = models.BigIntegerField(default=0, help_text="tiyin (100 tiyin = 1 so‘m)")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "oygul_bouquets"
        verbose_name = "bouquet"
        verbose_name_plural = "bouquets"
        ordering = ("name",)

    @property
    def price_sum(self) -> int:
        return self.price // 100

    def __str__(self) -> str:
        return f"{self.name} — {self.price_sum:,} so‘m"


class OygulOrder(CrmModel):
    id = models.BigAutoField(primary_key=True)
    chat_id = models.BigIntegerField()
    customer_name = models.CharField(max_length=255, blank=True, default="")
    customer_username = models.CharField(max_length=255, null=True, blank=True)
    bouquet_name = models.CharField(max_length=255)
    bouquet_photo_url = models.TextField(blank=True, default="")
    bouquet_price_sum = models.BigIntegerField(default=0, help_text="so‘m")
    delivery_fee_sum = models.BigIntegerField(default=0, help_text="so‘m")
    recipient_name = models.CharField(max_length=255, blank=True, default="")
    recipient_phone = models.CharField(max_length=64, blank=True, default="")
    address = models.TextField(blank=True, default="")
    delivery_time = models.CharField(max_length=255, blank=True, default="")
    card_text = models.TextField(null=True, blank=True)
    is_surprise = models.BooleanField(default=False)
    extra_notes = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=32, default="pending")
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "oygul_orders"
        verbose_name = "order"
        verbose_name_plural = "orders"
        ordering = ("-created_at",)

    @property
    def total_sum(self) -> int:
        return self.bouquet_price_sum + self.delivery_fee_sum

    def __str__(self) -> str:
        return f"#{self.id} {self.bouquet_name} — {self.status}"
