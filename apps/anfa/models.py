"""Data model for the anfa tenant — a flat priced service catalog.

The clinic runs its own local-only CRM and handles patient registration
offline. They periodically export their full service list (specialities, lab
tests, procedures, …) as an Excel file and share it with us; our system ingests
that export and the agent advises clients on services + prices, then sends them
to walk into the clinic. There is no online booking and no doctor/visit data
here anymore — just the catalog.

One table, declared on the shared `db.models.Base` so it shares the one database
and Alembic sees it. Keep this module import-light (stdlib + sqlalchemy + `db`):
the repository and, in turn, the admin panel import it and must not pull in the
agent/Telegram runtime.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base


def catalog_item_id(tab: str, category: str, title: str) -> int:
    """Deterministic, stable id for a catalog row.

    Derived from the row's identity (tab + category + title) so re-importing the
    same export keeps ids stable — the KB sync then only re-embeds rows whose
    *price/text* actually changed, instead of churning the whole index. 7 bytes
    of SHA-1 → a positive value that fits comfortably in a signed BigInteger.
    """
    key = f"{tab}|{category}|{title}".encode("utf-8")
    return int.from_bytes(hashlib.sha1(key).digest()[:7], "big")


def content_hash(tab: str, category: str, title: str, price: int) -> str:
    """Hash of everything that affects the indexed document (incl. price)."""
    raw = f"{tab}|{category}|{title}|{price}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class AnfaCatalogItem(Base):
    """One priced service from the clinic's Excel export.

    `tab` is the export's top-level grouping (Прием / Лаборатория / Услуги /
    Диагностика / Операционный / Группа); `category` is the speciality for
    `Прием` rows (e.g. "Хирург") and empty for the rest. `price` is in whole
    UZS sum (0 means free or unpriced in the source).
    """

    __tablename__ = "anfa_catalog"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tab: Mapped[str] = mapped_column(String(64), default="", index=True)
    category: Mapped[str] = mapped_column(String(255), default="", index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="UZS")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_kb_dict(self) -> dict:
        """Shape consumed by `kb_index.item_to_document` for the KB sync."""
        return {
            "id": self.id,
            "tab": self.tab,
            "category": self.category,
            "title": self.title,
            "price": self.price,
            "currency": self.currency,
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tab": self.tab,
            "category": self.category,
            "title": self.title,
            "price": self.price,
            "currency": self.currency,
            "active": self.active,
        }


def doctor_id(fullname: str) -> int:
    """Deterministic, stable id for a doctor row (keyed on full name)."""
    return int.from_bytes(hashlib.sha1(fullname.strip().encode("utf-8")).digest()[:7], "big")


def doctor_content_hash(fullname: str, speciality: str, experience: str, hours_label: str) -> str:
    raw = f"{fullname}|{speciality}|{experience}|{hours_label}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class AnfaDoctor(Base):
    """One physician from the clinic's doctor roster (a Word-doc export).

    This is *reference* data — the agent names the doctor, their experience,
    and when they receive patients (`hours_label` / `schedule`). There is no
    booking: `schedule` is stored as {weekday: [start_hour, end_hour]} (weekday
    0=Mon…6=Sun) only so the agent can reason about walk-in timing; `hours_label`
    is the ready-to-show, language-neutral version (e.g. "Mon–Sat 09:00–14:00").
    """

    __tablename__ = "anfa_doctors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    fullname: Mapped[str] = mapped_column(String(255), default="")
    speciality: Mapped[str] = mapped_column(String(255), default="", index=True)
    experience: Mapped[str] = mapped_column(Text, default="")
    schedule: Mapped[dict] = mapped_column(JSONB, default=dict)
    hours_label: Mapped[str] = mapped_column(String(255), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_kb_dict(self) -> dict:
        """Shape consumed by `kb_index.doctor_to_document` for the KB sync."""
        return {
            "id": self.id,
            "fullname": self.fullname,
            "speciality": self.speciality,
            "experience": self.experience,
            "hours_label": self.hours_label,
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "fullname": self.fullname,
            "speciality": self.speciality,
            "experience": self.experience,
            "schedule": self.schedule or {},
            "hours_label": self.hours_label,
            "active": self.active,
        }
