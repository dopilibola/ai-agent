"""Data models for the oygul tenant.

Two layers live here:
  - lightweight **domain dataclasses** (`Bouquet`, `FlowerAmount`) used on the
    hot search path — built from Chroma metadata, never touch the DB; and
  - **SQLAlchemy ORM tables** (`OygulBouquet`, `OygulOrder`) — oygul's slice of
    the per-tenant CRM, declared on the *shared* `db.models.Base` so they live
    in the one Postgres database and Alembic autogenerate sees them.

Keep this module import-light (only stdlib + sqlalchemy + `db`): the admin panel
imports it (via `repository.py`) and must not pull in the agent/Telegram runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base


@dataclass
class FlowerAmount:
    flower_name: str
    quantity: int

    def to_dict(self) -> dict:
        return {"flower_name": self.flower_name, "quantity": self.quantity}


@dataclass
class Bouquet:
    id: str
    branch_id: str
    name: str
    description: str
    tags: list[str]
    products_spent: list[FlowerAmount]
    photo_url: str
    price: int  # stored in tiyin (100 tiyin = 1 sum)
    created_at: str

    @property
    def price_sum(self) -> int:
        return self.price // 100

    @classmethod
    def from_metadata(cls, meta: dict) -> "Bouquet":
        return cls(
            id=meta["bouquet_id"],
            branch_id=meta["branch_id"],
            name=meta["name"],
            description=meta["description"],
            tags=[t for t in meta["tags"].split(",") if t],
            products_spent=[
                FlowerAmount(**p)
                for p in json.loads(meta.get("products_spent_json", "[]"))
            ],
            photo_url=meta["photo_url"],
            price=meta["price"],
            created_at=meta["created_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "branch_id": self.branch_id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "products_spent": [p.to_dict() for p in self.products_spent],
            "photo_url": self.photo_url,
            "price": self.price_sum,
            "created_at": self.created_at,
        }

    def contains_flowers(self, flowers: list[str]) -> bool:
        """All-of substring match across the bouquet's flower list (case-insensitive)."""
        stored = ",".join(p.flower_name for p in self.products_spent).lower()
        return all(f.lower() in stored for f in flowers)


# ============================================================================
# ORM tables — oygul's CRM system of record (Postgres). Chroma stays the search
# index; these rows are the source of truth the admin panel reads/manages.
# ============================================================================


class OygulBouquet(Base):
    """A catalogue bouquet. `price` is in **tiyin** (100 tiyin = 1 sum), matching
    the `Bouquet` dataclass and the Chroma metadata."""

    __tablename__ = "oygul_bouquets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    branch_id: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    products_spent: Mapped[list] = mapped_column(JSONB, default=list)
    photo_url: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int] = mapped_column(BigInteger, default=0)  # tiyin
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @property
    def price_sum(self) -> int:
        return self.price // 100

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "branch_id": self.branch_id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags or []),
            "products_spent": list(self.products_spent or []),
            "photo_url": self.photo_url,
            "price": self.price_sum,
            "active": self.active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class OygulOrder(Base):
    """A customer order. Money fields are in **sum** (UZS), mirroring the values
    `notify_order_tool` already collects in `notifications.OrderDetails`."""

    __tablename__ = "oygul_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), default="")
    customer_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    bouquet_name: Mapped[str] = mapped_column(String(255))
    bouquet_photo_url: Mapped[str] = mapped_column(Text, default="")
    bouquet_price_sum: Mapped[int] = mapped_column(BigInteger, default=0)
    delivery_fee_sum: Mapped[int] = mapped_column(BigInteger, default=0)

    recipient_name: Mapped[str] = mapped_column(String(255), default="")
    recipient_phone: Mapped[str] = mapped_column(String(64), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    delivery_time: Mapped[str] = mapped_column(String(255), default="")
    card_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_surprise: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @property
    def total_sum(self) -> int:
        return self.bouquet_price_sum + self.delivery_fee_sum

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "customer_name": self.customer_name,
            "customer_username": self.customer_username,
            "bouquet_name": self.bouquet_name,
            "bouquet_photo_url": self.bouquet_photo_url,
            "bouquet_price_sum": self.bouquet_price_sum,
            "delivery_fee_sum": self.delivery_fee_sum,
            "total_sum": self.total_sum,
            "recipient_name": self.recipient_name,
            "recipient_phone": self.recipient_phone,
            "address": self.address,
            "delivery_time": self.delivery_time,
            "card_text": self.card_text,
            "is_surprise": self.is_surprise,
            "extra_notes": self.extra_notes,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
