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


@register.filter(name="dictkey")
def dictkey(mapping, key):
    """`{{ values|dictkey:field.key }}` — look a dict up by a variable key.

    Django's dot lookup cannot use a variable as the key, and the settings page
    pairs a fixed field list with a values dict, so it needs exactly this.
    """
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""


@register.simple_tag(name="t")
def translate_literal(text) -> str:
    """`{% t "Mijozlar" %}` — the panel's own two-language copy.

    Uzbek is the source string, so an untranslated phrase still reads correctly;
    see `crm/i18n.py` for why this is a table rather than gettext.
    """
    from crm.i18n import t as _t

    return _t(text)


@register.filter(name="t")
def translate_value(value) -> str:
    """`{{ row.segment_obj.label|t }}` — same table, for strings that come from
    Python rather than from the template."""
    from crm.i18n import t as _t

    return _t(value)


@register.filter(name="bosqich")
def bosqich(value) -> str:
    """`{{ r.lead_stage|bosqich }}` → "4-bosqich" / "stage 4".

    A filter rather than two template strings because the number sits on a
    different side of the word in each language, and "4 — stage" is what you get
    when you translate the two halves separately.
    """
    from crm.i18n import t as _t

    if value in (None, ""):
        return ""
    word = _t("bosqich")
    return f"stage {value}" if word == "stage" else f"{value}-{word}"


# Inline SVG icons for the side menu and page heads. Drawn here rather than
# fetched: the panel loads nothing from a CDN (see the charts note in CLAUDE.md).
_ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h5v-6h4v6h5V9.5"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.3-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/><path d="M18 14.8c2 .7 3.3 2.4 3.7 5.2"/>',
    "send": '<path d="M21 3 10 14"/><path d="M21 3 14.5 21l-4.5-7-7-4.5Z"/>',
    "book": '<path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H20v15H5.5A1.5 1.5 0 0 0 4 19.5Z"/><path d="M4 19.5A1.5 1.5 0 0 0 5.5 21H20"/><path d="M8 7h8"/>',
    "chart": '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20H2"/>',
    "pulse": '<path d="M2 12h4l3-8 4 16 3-8h6"/>',
    "script": '<path d="M8 3h11v14a3 3 0 0 1-3 3H6"/><path d="M5 3a2 2 0 0 0-2 2v2h5V5a2 2 0 0 0-2-2Z"/><path d="M11 8h5M11 12h5"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/>',
    "shield": '<path d="M12 3 4 6v6c0 4.5 3.4 8.2 8 9 4.6-.8 8-4.5 8-9V6Z"/><path d="m9 12 2 2 4-4"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>',
    "lock": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "archive": '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4"/>',
    "logout": '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 17l-5-5 5-5M5 12h11"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
}


@register.simple_tag(name="icon")
def icon(name: str, size: int = 18):
    from django.utils.safestring import mark_safe

    body = _ICONS.get(name, _ICONS["spark"])
    return mark_safe(
        f'<svg class="ico" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{body}</svg>'
    )
