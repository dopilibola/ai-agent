"""Stage-6 PDF voucher generation.

Builds the booking voucher (logo line, auto voucher number, patient name,
program, arrival date, prepayment, remaining balance, clinic address) entirely in
memory and returns the bytes, which the scheduler sends to the client as a
Telegram document. `reportlab` is imported lazily inside the builder so importing
this module stays cheap (mirrors the rag/voice/db lazy-import convention).

The voucher *number* is allocated by the repository from a Postgres sequence and
passed in — generation here is pure/deterministic and does no DB or network I/O.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Optional

_RU_MONTHS_GEN = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _fmt_amount(amount: Optional[int]) -> str:
    if amount is None:
        return "—"
    return f"{int(amount):,}".replace(",", " ") + " сум"


def _fmt_date(d: Optional[date]) -> str:
    if d is None:
        return "—"
    return f"{d.day} {_RU_MONTHS_GEN[d.month]} {d.year}"


def build_voucher_pdf(
    *,
    voucher_number: int,
    patient_name: str,
    program_title: str,
    arrival: Optional[date],
    prepayment_amount: Optional[int],
    remaining_amount: Optional[int],
    clinic_address: str,
    clinic_name: str = "BYD Medical",
) -> bytes:
    """Render the voucher to PDF bytes. Cyrillic is rendered with a Unicode font
    (DejaVuSans if available, else Helvetica as a last resort)."""
    from reportlab.lib.pagesizes import A5, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    font = _register_font()
    bold = font if font == "Helvetica" else font  # DejaVuSans has no separate bold reg here

    buf = BytesIO()
    page = landscape(A5)
    width, height = page
    c = canvas.Canvas(buf, pagesize=page)

    # Border
    c.setLineWidth(1.5)
    c.rect(8 * mm, 8 * mm, width - 16 * mm, height - 16 * mm)

    # Header
    c.setFont(bold, 20)
    c.drawCentredString(width / 2, height - 22 * mm, clinic_name)
    c.setFont(font, 11)
    c.drawCentredString(width / 2, height - 30 * mm, "Ваучер на бронирование")
    c.setFont(font, 10)
    c.drawCentredString(
        width / 2, height - 37 * mm, f"№ {int(voucher_number)}"
    )

    # Body rows
    rows = [
        ("Пациент", patient_name or "—"),
        ("Программа", program_title or "—"),
        ("Дата заезда", _fmt_date(arrival)),
        ("Предоплата", _fmt_amount(prepayment_amount)),
        ("Остаток при заезде", _fmt_amount(remaining_amount)),
        ("Адрес клиники", clinic_address or "—"),
    ]
    y = height - 52 * mm
    label_x = 18 * mm
    value_x = 70 * mm
    c.setFont(font, 11)
    for label, value in rows:
        c.drawString(label_x, y, f"{label}:")
        c.drawString(value_x, y, str(value))
        y -= 10 * mm

    c.setFont(font, 8)
    c.drawCentredString(
        width / 2, 14 * mm,
        "Сохраните этот ваучер — он подтверждает бронь вашего места.",
    )

    c.showPage()
    c.save()
    return buf.getvalue()


def _register_font() -> str:
    """Register a Cyrillic-capable TrueType font and return its name; fall back
    to the built-in Helvetica (latin-only) if none is available on the host."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        try:
            pdfmetrics.registerFont(TTFont("DejaVuSans", path))
            return "DejaVuSans"
        except Exception:
            continue
    return "Helvetica"
