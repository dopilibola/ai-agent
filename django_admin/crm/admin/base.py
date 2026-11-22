"""Shared admin building blocks.

`ReadOnlyAdmin` is the important one. Several of these tables are *records of
what happened* — the conversation corpus, payment state, Bitrix mirror ids —
and a panel that lets someone retype them is worse than no panel: it would
corrupt the evidence the agents are debugged from, or thank a client who never
paid. Those models get this base, which keeps the list and detail views while
refusing add/change/delete.
"""

from __future__ import annotations

import json

from django.contrib import admin
from django.utils.html import format_html


class CrmAdmin(admin.ModelAdmin):
    """Base for every mirrored table: no bulk delete by default.

    The stock "delete selected" action is one mis-click away from wiping funnel
    rows that a running scheduler still points at, so it is opt-in per model.
    """

    actions_on_top = True
    list_per_page = 50

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


class ReadOnlyAdmin(CrmAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def pretty_json(value) -> str:
    """Render a JSONB column readably in a detail view."""
    if value in (None, "", {}, []):
        return "—"
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(value)
    return format_html(
        '<pre style="white-space:pre-wrap;margin:0;max-width:70em">{}</pre>', text
    )


def money(value: int | None, divisor: int = 1) -> str:
    """Format an amount with thin spaces; `divisor=100` converts tiyin to so‘m."""
    if value is None:
        return "—"
    return f"{value // divisor:,}".replace(",", " ") + " so‘m"
