"""Postgres data access for oygul's domain (catalogue + orders).

This is the single source of DB access for the oygul tenant, used by **both**
sides of the system:

  - the **bot runtime** — `add_bouquet_tool` writes catalogue rows here, and
    `notify_order_tool` / `update_order_status_tool` create + advance orders; and
  - the **admin panel** — its routers read the catalogue/orders and manage them
    (deactivate a bouquet, flip an order's status) through the same class.

Import-light on purpose: this module pulls in only `sqlalchemy` (via the ORM
models) + `db.engine`, never `services.py`/`tools.py`. That lets the admin panel
import it without dragging in the agent/Telegram runtime (CLIP, telethon,
langchain). All access goes through the shared async engine (`db.engine`), so a
single Postgres pool is reused per process.

Postgres-only: every method assumes `DATABASE_URL` is set (the engine raises
otherwise). The JSON / in-memory fallback used when Postgres is unconfigured is
the caller's concern — see `services.get_repository()`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from apps.oygul.models import OygulBouquet, OygulOrder
from db.engine import get_sessionmaker


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OygulRepository:
    # ----- catalogue ----------------------------------------------------

    async def add_bouquet(
        self,
        *,
        id: str,
        name: str,
        price: int,
        branch_id: str = "",
        description: str = "",
        tags: Optional[list[str]] = None,
        products_spent: Optional[list[dict]] = None,
        photo_url: str = "",
    ) -> OygulBouquet:
        """Insert a bouquet (idempotent upsert on `id`). `price` is in tiyin.

        Re-adding an existing id refreshes its fields (e.g. after a re-embed)
        but preserves `created_at`.
        """
        from sqlalchemy.dialects.postgresql import insert

        now = _now()
        values = {
            "id": id,
            "branch_id": branch_id,
            "name": name,
            "description": description,
            "tags": tags or [],
            "products_spent": products_spent or [],
            "photo_url": photo_url,
            "price": int(price),
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
        # On conflict, update everything except the primary key + created_at.
        updatable = {
            k: values[k]
            for k in values
            if k not in ("id", "created_at")
        }
        stmt = (
            insert(OygulBouquet)
            .values(**values)
            .on_conflict_do_update(index_elements=["id"], set_=updatable)
        )
        async with get_sessionmaker()() as session:
            await session.execute(stmt)
            await session.commit()
        return await self.get_bouquet(id)  # type: ignore[return-value]

    @staticmethod
    def _bouquet_where(stmt, include_inactive: bool, search: Optional[str]):
        if not include_inactive:
            stmt = stmt.where(OygulBouquet.active.is_(True))
        if search:
            stmt = stmt.where(OygulBouquet.name.ilike(f"%{search}%"))
        return stmt

    async def list_bouquets(
        self,
        *,
        include_inactive: bool = False,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OygulBouquet]:
        from sqlalchemy import select

        stmt = self._bouquet_where(select(OygulBouquet), include_inactive, search)
        stmt = stmt.order_by(OygulBouquet.created_at.desc()).limit(limit).offset(offset)
        async with get_sessionmaker()() as session:
            rows = await session.scalars(stmt)
            return list(rows)

    async def count_bouquets(
        self, *, include_inactive: bool = False, search: Optional[str] = None
    ) -> int:
        from sqlalchemy import func, select

        stmt = self._bouquet_where(
            select(func.count(OygulBouquet.id)), include_inactive, search
        )
        async with get_sessionmaker()() as session:
            return int(await session.scalar(stmt) or 0)

    async def upsert_bouquets_from_metadata(
        self, metadatas: list[dict], *, batch: int = 500
    ) -> int:
        """Upsert `oygul_bouquets` rows from Chroma-shaped metadata dicts (the
        shape `Bouquet.from_metadata` reads). Lets the catalogue ingest + the
        backfill keep the Postgres source of truth in step with the Chroma
        search index. Existing `created_at` is preserved on conflict."""
        from datetime import datetime, timezone

        from sqlalchemy.dialects.postgresql import insert

        from apps.oygul.models import Bouquet

        now = datetime.now(timezone.utc)

        def _parse_dt(raw) -> datetime:
            try:
                d = datetime.fromisoformat(raw)
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return now

        written = 0
        pending: list[dict] = []

        async def _flush() -> None:
            nonlocal written
            if not pending:
                return
            stmt = insert(OygulBouquet).values(pending)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "branch_id": stmt.excluded.branch_id,
                    "name": stmt.excluded.name,
                    "description": stmt.excluded.description,
                    "tags": stmt.excluded.tags,
                    "products_spent": stmt.excluded.products_spent,
                    "photo_url": stmt.excluded.photo_url,
                    "price": stmt.excluded.price,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            async with get_sessionmaker()() as session:
                await session.execute(stmt)
                await session.commit()
            written += len(pending)
            pending.clear()

        for meta in metadatas:
            if not meta:
                continue
            try:
                b = Bouquet.from_metadata(meta)
            except Exception:
                continue
            pending.append(
                {
                    "id": b.id,
                    "branch_id": b.branch_id,
                    "name": b.name,
                    "description": b.description,
                    "tags": b.tags,
                    "products_spent": [p.to_dict() for p in b.products_spent],
                    "photo_url": b.photo_url,
                    "price": b.price,  # tiyin
                    "active": True,
                    "created_at": _parse_dt(b.created_at),
                    "updated_at": now,
                }
            )
            if len(pending) >= batch:
                await _flush()
        await _flush()
        return written

    async def get_bouquet(self, bouquet_id: str) -> Optional[OygulBouquet]:
        async with get_sessionmaker()() as session:
            return await session.get(OygulBouquet, bouquet_id)

    async def deactivate_bouquet(self, bouquet_id: str) -> bool:
        """Soft-delete: take the bouquet off sale, keeping its row + photo so it
        can be reactivated without re-embedding.

        The Chroma embedding is intentionally left in place; customer search
        excludes inactive ids (see `inactive_bouquet_ids` + `search_bouquets_tool`),
        so a deactivated bouquet stops appearing without a cross-process Chroma
        write from the admin panel.
        """
        return await self._set_active(bouquet_id, False)

    async def reactivate_bouquet(self, bouquet_id: str) -> bool:
        """Put a previously-deactivated bouquet back on sale. No re-embed needed
        — its Chroma entry was never removed."""
        return await self._set_active(bouquet_id, True)

    async def _set_active(self, bouquet_id: str, active: bool) -> bool:
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(OygulBouquet)
                .where(OygulBouquet.id == bouquet_id)
                .values(active=active, updated_at=_now())
            )
            await session.commit()
            return bool(result.rowcount)

    async def inactive_bouquet_ids(self) -> set[str]:
        """Ids of bouquets currently off sale — used to filter them out of
        customer search (the Chroma index still holds their embeddings)."""
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(OygulBouquet.id).where(OygulBouquet.active.is_(False))
            )
            return set(rows)

    # ----- orders -------------------------------------------------------

    async def create_order(
        self,
        *,
        chat_id: int,
        bouquet_name: str,
        bouquet_price_sum: int,
        delivery_fee_sum: int,
        recipient_name: str,
        recipient_phone: str,
        address: str,
        delivery_time: str,
        customer_name: str = "",
        customer_username: Optional[str] = None,
        bouquet_photo_url: str = "",
        card_text: Optional[str] = None,
        is_surprise: bool = False,
        extra_notes: Optional[str] = None,
        status: str = "pending",
    ) -> OygulOrder:
        now = _now()
        order = OygulOrder(
            chat_id=int(chat_id),
            customer_name=customer_name,
            customer_username=customer_username,
            bouquet_name=bouquet_name,
            bouquet_photo_url=bouquet_photo_url,
            bouquet_price_sum=int(bouquet_price_sum),
            delivery_fee_sum=int(delivery_fee_sum),
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            address=address,
            delivery_time=delivery_time,
            card_text=card_text,
            is_surprise=is_surprise,
            extra_notes=extra_notes,
            status=status,
            created_at=now,
            updated_at=now,
        )
        async with get_sessionmaker()() as session:
            session.add(order)
            await session.commit()
        return order  # id is populated by the flush on commit

    async def list_orders(
        self, *, status: Optional[str] = None, limit: int = 200
    ) -> list[OygulOrder]:
        from sqlalchemy import select

        stmt = select(OygulOrder)
        if status is not None:
            stmt = stmt.where(OygulOrder.status == status)
        stmt = stmt.order_by(OygulOrder.created_at.desc()).limit(limit)
        async with get_sessionmaker()() as session:
            rows = await session.scalars(stmt)
            return list(rows)

    async def orders_for_chat(self, chat_id: int) -> list[OygulOrder]:
        from sqlalchemy import select

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(OygulOrder)
                .where(OygulOrder.chat_id == int(chat_id))
                .order_by(OygulOrder.created_at.desc())
            )
            return list(rows)

    async def get_order(self, order_id: int) -> Optional[OygulOrder]:
        async with get_sessionmaker()() as session:
            return await session.get(OygulOrder, int(order_id))

    async def set_order_status(self, order_id: int, status: str) -> bool:
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(OygulOrder)
                .where(OygulOrder.id == int(order_id))
                .values(status=status, updated_at=_now())
            )
            await session.commit()
            return bool(result.rowcount)

    async def set_status_for_chat(self, chat_id: int, status: str) -> int:
        """Advance every order belonging to a chat (mirrors how
        `update_order_status_tool` fans a status change across a chat's orders).
        Returns the number of rows updated."""
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(OygulOrder)
                .where(OygulOrder.chat_id == int(chat_id))
                .values(status=status, updated_at=_now())
            )
            await session.commit()
            return int(result.rowcount or 0)


_repository: Optional[OygulRepository] = None


def get_repository() -> OygulRepository:
    """Module-level singleton. Stateless (the engine pool is shared), so a single
    instance is fine for both the bot process and the admin panel."""
    global _repository
    if _repository is None:
        _repository = OygulRepository()
    return _repository
