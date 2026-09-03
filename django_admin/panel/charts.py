"""Inline SVG charts, rendered server-side.

No charting library and no CDN: the panel runs on an internal host that may
have no outbound network, and a chart that silently fails to load is worse than
a plain table. Every chart here is a handful of SVG elements sized by a
viewBox, so it scales with its container, prints, and needs no JavaScript.

Colours come from the admin's own CSS variables where possible so the charts
follow light/dark mode with the rest of the page; the categorical colours are
fixed hex values chosen to stay legible on both.
"""

from __future__ import annotations

from datetime import date
from html import escape

from django.utils.safestring import mark_safe

# Categorical palette — distinguishable, and readable on both themes.
INK = "var(--body-fg, #333)"
MUTED = "var(--body-quiet-color, #888)"
GRID = "var(--hairline-color, #e4e4e4)"


def _fmt(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return f"{n:g}"


def stacked_bars(
    labels: list[date],
    series: list[tuple[str, list[int], str]],
    *,
    height: int = 150,
    label_every: int = 5,
) -> str:
    """Daily stacked bars. `series` is [(name, values, colour), …]."""
    n = len(labels)
    if not n:
        return mark_safe('<p class="chart-empty">Ma’lumot yo‘q</p>')

    width = 720
    pad_l, pad_b, pad_t = 34, 20, 8
    plot_w = width - pad_l - 6
    plot_h = height - pad_b - pad_t
    totals = [sum(s[1][i] for s in series) for i in range(n)]
    top = max(totals) or 1
    slot = plot_w / n
    bar_w = max(2.0, slot * 0.72)

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'preserveAspectRatio="none">'
    ]
    # Three gridlines is enough to read a magnitude without clutter.
    for frac in (0, 0.5, 1.0):
        y = pad_t + plot_h - plot_h * frac
        value = top * frac
        parts.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - 6}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_l - 5}" y="{y + 3.5:.1f}" text-anchor="end" '
            f'font-size="9" fill="{MUTED}">{_fmt(value)}</text>'
        )

    for i, label in enumerate(labels):
        x = pad_l + slot * i + (slot - bar_w) / 2
        y = pad_t + plot_h
        tip = " · ".join(f"{name}: {vals[i]}" for name, vals, _ in series)
        parts.append(f"<g><title>{escape(str(label))} — {escape(tip)}</title>")
        for _name, values, colour in series:
            v = values[i]
            if not v:
                continue
            h = plot_h * v / top
            y -= h
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                f'height="{h:.1f}" fill="{colour}" rx="1"/>'
            )
        parts.append("</g>")
        if i % label_every == 0 or i == n - 1:
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{height - 6}" '
                f'text-anchor="middle" font-size="9" fill="{MUTED}">'
                f"{label.strftime('%d.%m')}</text>"
            )
    parts.append("</svg>")
    return mark_safe("".join(parts))


def hbars(items: list[tuple[str, int]], *, colour: str = "#2b7de9", limit: int = 12) -> str:
    """Horizontal bars for a ranked breakdown (segments, actions, top chats)."""
    items = [i for i in items if i[1]][:limit]
    if not items:
        return mark_safe('<p class="chart-empty">Ma’lumot yo‘q</p>')
    top = max(v for _, v in items) or 1
    rows = []
    for label, value in items:
        pct = value / top * 100
        rows.append(
            '<div class="hbar-row">'
            f'<span class="hbar-label" title="{escape(label)}">{escape(label)}</span>'
            f'<span class="hbar-track"><span class="hbar-fill" '
            f'style="width:{pct:.1f}%;background:{colour}"></span></span>'
            f'<span class="hbar-value">{value:,}</span>'.replace(",", " ")
            + "</div>"
        )
    return mark_safe('<div class="hbars">' + "".join(rows) + "</div>")


def segment_bar(counts: list[tuple[object, int]], *, base: str = "") -> str:
    """A single stacked strip showing how the audience splits by category.

    `base` is where a legend entry links to. It defaults to the current page,
    which is right on the directory (the legend *is* its filter) and wrong on
    the dashboard, which has no filter — so that page passes the directory's URL.
    """
    total = sum(v for _, v in counts) or 1
    cells = []
    legend = []
    for seg, value in counts:
        if not value:
            continue
        pct = value / total * 100
        cells.append(
            f'<span class="seg-cell" style="width:{pct:.2f}%;background:{seg.color}" '
            f'title="{escape(seg.label)}: {value} ({pct:.0f}%)"></span>'
        )
        legend.append(
            f'<a class="seg-legend-item" href="{base}?segment={seg.key}">'
            f'<i style="background:{seg.color}"></i>{escape(seg.label)}'
            f"<b>{value}</b></a>"
        )
    return mark_safe(
        '<div class="seg-bar">' + "".join(cells) + "</div>"
        '<div class="seg-legend">' + "".join(legend) + "</div>"
    )


def sparkline(values: list[int], *, colour: str = "#2b7de9", width: int = 120, height: int = 28) -> str:
    """A tiny trend line for a table cell or a card."""
    if not values or max(values) == 0:
        return mark_safe(f'<svg class="spark" viewBox="0 0 {width} {height}"></svg>')
    top = max(values)
    step = width / max(1, len(values) - 1)
    pts = " ".join(
        f"{i * step:.1f},{height - 2 - (height - 4) * v / top:.1f}"
        for i, v in enumerate(values)
    )
    return mark_safe(
        f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        f'<polyline points="{pts}" fill="none" stroke="{colour}" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )
