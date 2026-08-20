"""Parse the clinic's Word doctor roster and load it into the doctors table.

The clinic supplied its physician list as a `.docx` (Uzbek): a table with
columns № | FISH (full name) | Mutahassislik (speciality) | Toifa va staj
(category + experience) | Qabul vaqtlari (reception hours). This is *reference*
data — the catalog agent names the doctor and tells clients when to walk in;
there is no booking.

We parse the reception-hours cell into a structured weekly schedule
({weekday: [start_hour, end_hour]}, 0=Mon) AND a clean language-neutral label
("Mon–Sat 09:00–14:00") that the agent shows and localises. Rows with no
speciality (the 24/7 home brigades) are skipped — those are services and live
in the Excel catalog.

Import-light: `python-docx` is imported lazily inside `parse_docx`.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Uzbek weekday abbreviations/full forms → Python weekday (0=Mon … 6=Sun).
_WEEKDAY = {
    "du": 0, "dushanba": 0,
    "se": 1, "seshanba": 1,
    "chor": 2, "chorshanba": 2,
    "pay": 3, "payshanba": 3,
    "ju": 4, "juma": 4,
    "shan": 5, "shanba": 5,
    "yak": 6, "yakshanba": 6,
}
_WEEKDAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_HOURS_RE = re.compile(r"(\d{1,2})[:.](\d{2})\s*[-–]\s*(\d{1,2})[:.](\d{2})")
_RANGE_RE = re.compile(r"([a-z]+)\s*[-–]\s*([a-z]+)")


def _parse_days(text: str) -> list[int]:
    t = text.lower().replace(".", " ")
    m = _RANGE_RE.search(t)
    if m and m.group(1) in _WEEKDAY and m.group(2) in _WEEKDAY:
        a, b = _WEEKDAY[m.group(1)], _WEEKDAY[m.group(2)]
        if a <= b:
            return list(range(a, b + 1))
    return [_WEEKDAY[tok] for tok in re.findall(r"[a-z]+", t) if tok in _WEEKDAY]


def _parse_hours(text: str) -> Optional[tuple[int, int]]:
    m = _HOURS_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(3))


def parse_schedule(cell: str) -> tuple[dict[str, list[int]], str]:
    """Parse a 'Qabul vaqtlari' cell → ({weekday: [start,end]}, display label).

    Handles the two shapes seen in the export: a days line followed by an hours
    line ("Du.-Shan." / "09:00-14:00"), and per-line day+hours pairs
    ("Chor. 09:00-13:00" / "Shan. 09:00-16:00"). Falls back to an empty schedule
    with the raw text as label if it can't be parsed.
    """
    schedule: dict[str, list[int]] = {}
    current_days: list[int] = []
    for line in cell.splitlines():
        line = line.strip()
        if not line:
            continue
        days = _parse_days(line)
        hours = _parse_hours(line)
        if days:
            current_days = days
        if hours and current_days:
            for wd in current_days:
                schedule[str(wd)] = [hours[0], hours[1]]
            current_days = []
    label = _format_label(schedule) if schedule else " ".join(cell.split())
    return schedule, label


def _format_label(schedule: dict[str, list[int]]) -> str:
    """Group weekdays by identical hours → 'Mon–Sat 09:00–14:00; Sun 10:00–16:00'."""
    by_hours: dict[tuple[int, int], list[int]] = {}
    for wd_str, hrs in schedule.items():
        by_hours.setdefault((hrs[0], hrs[1]), []).append(int(wd_str))
    parts: list[str] = []
    for (start, end), wds in sorted(by_hours.items(), key=lambda kv: min(kv[1])):
        wds = sorted(wds)
        # compress consecutive weekdays into a range
        spans: list[str] = []
        i = 0
        while i < len(wds):
            j = i
            while j + 1 < len(wds) and wds[j + 1] == wds[j] + 1:
                j += 1
            spans.append(
                _WEEKDAY_EN[wds[i]]
                if i == j
                else f"{_WEEKDAY_EN[wds[i]]}–{_WEEKDAY_EN[wds[j]]}"
            )
            i = j + 1
        parts.append(f"{', '.join(spans)} {start:02d}:00–{end:02d}:00")
    return "; ".join(parts)


def parse_docx(data: bytes) -> list[dict]:
    """Parse the roster .docx → list of doctor dicts.

    Reads the first table with a FISH/Mutahassislik header, maps columns by
    header, skips rows without a full name or without a speciality (the 24/7
    home brigades), and de-duplicates by full name. Raises ValueError if no
    doctor table is found.
    """
    import docx  # lazy — keeps module import cheap

    document = docx.Document(io.BytesIO(data))
    for table in document.tables:
        header = [c.text.strip().lower() for c in table.rows[0].cells]
        if not any("fish" in h or "f.i.sh" in h for h in header):
            continue
        # locate columns by header label
        idx = {"fullname": None, "speciality": None, "experience": None, "hours": None}
        for i, h in enumerate(header):
            if "fish" in h or "f.i.sh" in h:
                idx["fullname"] = i
            elif "mutaxassis" in h or "mutahassis" in h:
                idx["speciality"] = i
            elif "toifa" in h or "staj" in h:
                idx["experience"] = i
            elif "qabul" in h or "vaqt" in h:
                idx["hours"] = i
        if idx["fullname"] is None:
            continue

        by_name: dict[str, dict] = {}
        for row in table.rows[1:]:
            cells = [c.text.strip() for c in row.cells]

            def cell(key: str) -> str:
                i = idx[key]
                return cells[i] if i is not None and i < len(cells) else ""

            fullname = " ".join(cell("fullname").split())
            speciality = " ".join(cell("speciality").replace("\n", " ").split())
            if not fullname or not speciality:
                continue  # skips the speciality-less 24/7 home brigades
            experience = " ".join(cell("experience").replace("\n", ". ").split())
            schedule, hours_label = parse_schedule(cell("hours"))
            by_name[fullname] = {
                "fullname": fullname,
                "speciality": speciality,
                "experience": experience,
                "schedule": schedule,
                "hours_label": hours_label,
            }
        return list(by_name.values())

    raise ValueError(
        "No doctor table found — expected a table with FISH / Mutahassislik columns."
    )


async def import_docx_bytes(data: bytes, repo: Optional[object] = None) -> dict:
    """Parse the roster and reconcile it into the doctors table."""
    from apps.anfa.repository import get_repository

    rows = parse_docx(data)
    repository = repo or get_repository()
    summary = await repository.replace_doctors(rows)
    summary["parsed"] = len(rows)
    logger.info(
        "Doctor import: parsed=%d added=%d updated=%d removed=%d total=%d",
        summary["parsed"],
        summary["added"],
        summary["updated"],
        summary["removed"],
        summary["total"],
    )
    return summary
