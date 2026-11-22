"""Anfa — Tashkent clinic. Mirrors `apps/anfa/models.py`.

Both tables are *reference* data reconciled whole on each import, with ids
derived from content (`sha1(tab|category|title)`, `sha1(fullname)`) rather than
a sequence — so a re-import is incremental instead of churning every row. That
is why `id` is a plain BigIntegerField here and not an auto field: nothing in
this database hands out these ids.

There is no visit or availability data and no online booking; a doctor's
`hours_label` is a walk-in time, not a bookable slot.
"""

from __future__ import annotations

from django.db import models

from .base import CrmModel


class AnfaCatalogItem(CrmModel):
    id = models.BigIntegerField(primary_key=True)
    tab = models.CharField(max_length=64, blank=True, default="")
    category = models.CharField(max_length=255, blank=True, default="")
    title = models.TextField(blank=True, default="")
    price = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=8, default="UZS")
    active = models.BooleanField(default=True)
    content_hash = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "anfa_catalog"
        verbose_name = "service"
        verbose_name_plural = "service catalog"
        ordering = ("tab", "category", "title")

    def __str__(self) -> str:
        return f"{self.title} — {self.price:,} {self.currency}"


class AnfaDoctor(CrmModel):
    id = models.BigIntegerField(primary_key=True)
    fullname = models.CharField(max_length=255, blank=True, default="")
    speciality = models.CharField(max_length=255, blank=True, default="")
    experience = models.TextField(blank=True, default="")
    schedule = models.JSONField(default=dict)
    hours_label = models.CharField(max_length=255, blank=True, default="")
    active = models.BooleanField(default=True)
    content_hash = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "anfa_doctors"
        verbose_name = "doctor"
        verbose_name_plural = "doctors"
        ordering = ("speciality", "fullname")

    def __str__(self) -> str:
        return f"{self.fullname} — {self.speciality}"
