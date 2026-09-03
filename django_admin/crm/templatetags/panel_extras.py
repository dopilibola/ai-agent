"""Template filters for the panel's pages.

Number grouping is done here rather than through `USE_THOUSAND_SEPARATOR`
because that setting defers to the active locale's format module, and the
separator would then change with `DJANGO_LANGUAGE_CODE`. Amounts on this panel
are money and token counts read at a glance — they should group the same way in
every language.
"""

from __future__ import annotations

from django import template

register = template.Library()

# A narrow no-break space: groups digits without the comma/period ambiguity
# between locales, and never wraps mid-number.
GROUP_SEP = " "


@register.filter(name="son")
def son(value) -> str:
    """Group an integer: 891170 → "891 170". Non-numbers pass through as text."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    return f"{number:,}".replace(",", GROUP_SEP)


@register.filter(name="pul")
def pul(value) -> str:
    """Money in so‘m, grouped: 70000 → "70 000 so‘m"."""
    if value in (None, ""):
        return "—"
    return f"{son(value)} so‘m"


@register.filter(name="amal")
def amal(value) -> str:
    """A scheduled action's raw key → its plain-Uzbek name.

    Kept as a filter so the queue table and the analytics touch-mix chart read
    the same way without either page having to build a lookup dict.
    """
    from crm.outreach import action_label

    return action_label(str(value or ""))


@register.filter(name="rol")
def rol(value) -> str:
    """A `conversation_events.role` → who that was, in Uzbek."""
    return {
        "user": "mijoz",
        "assistant": "AI",
        "outbound": "avto-xabar",
        "operator": "operator",
        "tool": "vosita",
        "outcome": "holat",
    }.get(str(value or ""), str(value or ""))
