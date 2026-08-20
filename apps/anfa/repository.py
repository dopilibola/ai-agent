"""Postgres data access for anfa's service catalog.

The catalog is fed by the clinic's Excel export (see `import_catalog.py`) and is
our system of record for what the agent advises clients about. This is the
single data layer used by both the bot runtime (search/KB sync) and the admin
panel.

Import-light on purpose (only sqlalchemy + db.engine + apps.anfa.{models,config}):
the admin panel imports it without dragging in the agent/Telegram runtime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from apps.anfa.config import CLINIC_TZ
from apps.anfa.models import (
    AnfaCatalogItem,
    AnfaDoctor,
    catalog_item_id,
    content_hash,
    doctor_content_hash,
    doctor_id,
)
from db.engine import get_sessionmaker


def _now() -> datetime:
    return datetime.now(CLINIC_TZ)


class AnfaRepository:
    # ----- reads --------------------------------------------------------

    async def list_catalog(self, *, active_only: bool = True) -> list[AnfaCatalogItem]:
        from sqlalchemy import select

        stmt = select(AnfaCatalogItem)
        if active_only:
            stmt = stmt.where(AnfaCatalogItem.active.is_(True))
        stmt = stmt.order_by(
            AnfaCatalogItem.tab, AnfaCatalogItem.category, AnfaCatalogItem.title
        )
        async with get_sessionmaker()() as session:
            return list(await session.scalars(stmt))

    async def get_item(self, item_id) -> Optional[AnfaCatalogItem]:
        async with get_sessionmaker()() as session:
            return await session.get(AnfaCatalogItem, int(item_id))

    async def list_categories(self) -> list[dict]:
        """Distinct (tab, category) groups with a row count — for browsing/admin."""
        from sqlalchemy import func, select

        stmt = (
            select(
                AnfaCatalogItem.tab,
                AnfaCatalogItem.category,
                func.count().label("n"),
            )
            .where(AnfaCatalogItem.active.is_(True))
            .group_by(AnfaCatalogItem.tab, AnfaCatalogItem.category)
            .order_by(AnfaCatalogItem.tab, AnfaCatalogItem.category)
        )
        async with get_sessionmaker()() as session:
            rows = await session.execute(stmt)
        return [{"tab": t, "category": c, "count": n} for t, c, n in rows]

    # ----- writes -------------------------------------------------------

    async def find_items(
        self, query: str, *, limit: int = 10, active_only: bool = False
    ) -> list[AnfaCatalogItem]:
        """Substring search over catalog title/category — for the manager to
        locate a row (and its id) before editing its price. Case-insensitive."""
        from sqlalchemy import or_, select

        like = f"%{query.strip()}%"
        stmt = select(AnfaCatalogItem).where(
            or_(
                AnfaCatalogItem.title.ilike(like),
                AnfaCatalogItem.category.ilike(like),
            )
        )
        if active_only:
            stmt = stmt.where(AnfaCatalogItem.active.is_(True))
        stmt = stmt.order_by(
            AnfaCatalogItem.tab, AnfaCatalogItem.category, AnfaCatalogItem.title
        ).limit(max(1, limit))
        async with get_sessionmaker()() as session:
            return list(await session.scalars(stmt))

    async def set_item_price(self, item_id, price: int) -> Optional[AnfaCatalogItem]:
        """Update one catalog row's price. Recomputes `content_hash` so the KB
        sync re-embeds it, and returns the refreshed row (for immediate KB
        indexing by the caller) or None if the id doesn't exist."""
        async with get_sessionmaker()() as session:
            item = await session.get(AnfaCatalogItem, int(item_id))
            if item is None:
                return None
            item.price = int(price)
            item.content_hash = content_hash(
                item.tab, item.category, item.title, item.price
            )
            item.updated_at = _now()
            await session.commit()
            await session.refresh(item)
            return item

    async def set_item_active(self, item_id, active: bool) -> bool:
        """Toggle one catalog row. Deactivated rows drop out of the KB on the
        next sync (`list_catalog(active_only=True)`)."""
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(AnfaCatalogItem)
                .where(AnfaCatalogItem.id == int(item_id))
                .values(active=active, updated_at=_now())
            )
            await session.commit()
            return bool(result.rowcount)

    async def replace_catalog(self, rows: list[dict]) -> dict:
        """Reconcile the whole catalog against a fresh export (the source of
        truth). Upserts every row in `rows` and deletes any existing row whose
        id is no longer present. `rows` items are dicts with keys
        tab/category/title/price (+ optional currency); ids and content hashes
        are derived here so callers don't have to.

        Returns a summary: {added, updated, removed, total}. The whole
        reconcile runs in one transaction so a mid-import failure leaves the
        live catalog untouched.
        """
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert

        now = _now()
        prepared: dict[int, dict] = {}
        for r in rows:
            tab = (r.get("tab") or "").strip()
            category = (r.get("category") or "").strip()
            title = (r.get("title") or "").strip()
            if not title:
                continue
            price = int(r.get("price") or 0)
            currency = (r.get("currency") or "UZS").strip() or "UZS"
            iid = catalog_item_id(tab, category, title)
            # Last write wins on a duplicate identity within the same export.
            prepared[iid] = {
                "id": iid,
                "tab": tab,
                "category": category,
                "title": title,
                "price": price,
                "currency": currency,
                "active": True,
                "content_hash": content_hash(tab, category, title, price),
                "created_at": now,
                "updated_at": now,
            }

        new_ids = set(prepared)
        async with get_sessionmaker()() as session:
            existing = {
                iid: chash
                for iid, chash in await session.execute(
                    select(AnfaCatalogItem.id, AnfaCatalogItem.content_hash)
                )
            }
            added = updated = 0
            for iid, values in prepared.items():
                if iid not in existing:
                    added += 1
                elif existing[iid] != values["content_hash"]:
                    updated += 1
                stmt = insert(AnfaCatalogItem).values(**values).on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        k: values[k]
                        for k in values
                        if k not in ("id", "created_at")
                    },
                )
                await session.execute(stmt)

            stale = [iid for iid in existing if iid not in new_ids]
            removed = 0
            if stale:
                from sqlalchemy import delete

                result = await session.execute(
                    delete(AnfaCatalogItem).where(AnfaCatalogItem.id.in_(stale))
                )
                removed = result.rowcount or 0
            await session.commit()

        return {"added": added, "updated": updated, "removed": removed, "total": len(prepared)}


    # ----- doctors (reference roster) -----------------------------------

    async def list_doctors(self, *, active_only: bool = True) -> list[AnfaDoctor]:
        from sqlalchemy import select

        stmt = select(AnfaDoctor)
        if active_only:
            stmt = stmt.where(AnfaDoctor.active.is_(True))
        stmt = stmt.order_by(AnfaDoctor.speciality, AnfaDoctor.fullname)
        async with get_sessionmaker()() as session:
            return list(await session.scalars(stmt))

    async def get_doctor(self, doc_id) -> Optional[AnfaDoctor]:
        async with get_sessionmaker()() as session:
            return await session.get(AnfaDoctor, int(doc_id))

    async def set_doctor_active(self, doc_id, active: bool) -> bool:
        from sqlalchemy import update

        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(AnfaDoctor)
                .where(AnfaDoctor.id == int(doc_id))
                .values(active=active, updated_at=_now())
            )
            await session.commit()
            return bool(result.rowcount)

    async def upsert_doctor(self, data: dict) -> AnfaDoctor:
        """Add or update ONE doctor, keyed on full name (the id is derived from
        it — `doctor_id(fullname)`). Editing name/speciality/experience/hours of
        an existing card just re-supplies the same full name. Returns the saved
        row so the caller can immediately (re)index it into the KB.

        `data` keys: fullname (required), speciality, experience, hours_label,
        schedule. A rename lands as a NEW id — deactivate/delete the old card.
        """
        fullname = (data.get("fullname") or "").strip()
        if not fullname:
            raise ValueError("fullname is required")
        speciality = (data.get("speciality") or "").strip()
        experience = (data.get("experience") or "").strip()
        hours_label = (data.get("hours_label") or "").strip()
        did = doctor_id(fullname)
        now = _now()
        async with get_sessionmaker()() as session:
            doctor = await session.get(AnfaDoctor, did)
            if doctor is None:
                doctor = AnfaDoctor(id=did, created_at=now)
                session.add(doctor)
            doctor.fullname = fullname
            doctor.speciality = speciality
            doctor.experience = experience
            doctor.hours_label = hours_label
            doctor.schedule = data.get("schedule") or {}
            doctor.active = True
            doctor.content_hash = doctor_content_hash(
                fullname, speciality, experience, hours_label
            )
            doctor.updated_at = now
            await session.commit()
            await session.refresh(doctor)
            return doctor

    async def delete_doctor(self, doc_id) -> bool:
        """Hard-remove one doctor row. The KB doc is pruned on the next sync (or
        immediately by the caller)."""
        from sqlalchemy import delete

        async with get_sessionmaker()() as session:
            result = await session.execute(
                delete(AnfaDoctor).where(AnfaDoctor.id == int(doc_id))
            )
            await session.commit()
            return bool(result.rowcount)

    async def replace_doctors(self, rows: list[dict]) -> dict:
        """Reconcile the whole doctor roster against a fresh export — same
        upsert-present + delete-missing contract as `replace_catalog`. `rows`
        are dicts with keys fullname/speciality/experience/schedule/hours_label.
        """
        from sqlalchemy import delete, select
        from sqlalchemy.dialects.postgresql import insert

        now = _now()
        prepared: dict[int, dict] = {}
        for r in rows:
            fullname = (r.get("fullname") or "").strip()
            if not fullname:
                continue
            speciality = (r.get("speciality") or "").strip()
            experience = (r.get("experience") or "").strip()
            hours_label = (r.get("hours_label") or "").strip()
            did = doctor_id(fullname)
            prepared[did] = {
                "id": did,
                "fullname": fullname,
                "speciality": speciality,
                "experience": experience,
                "schedule": r.get("schedule") or {},
                "hours_label": hours_label,
                "active": True,
                "content_hash": doctor_content_hash(
                    fullname, speciality, experience, hours_label
                ),
                "created_at": now,
                "updated_at": now,
            }

        new_ids = set(prepared)
        async with get_sessionmaker()() as session:
            existing = {
                did: chash
                for did, chash in await session.execute(
                    select(AnfaDoctor.id, AnfaDoctor.content_hash)
                )
            }
            added = updated = 0
            for did, values in prepared.items():
                if did not in existing:
                    added += 1
                elif existing[did] != values["content_hash"]:
                    updated += 1
                stmt = insert(AnfaDoctor).values(**values).on_conflict_do_update(
                    index_elements=["id"],
                    set_={k: values[k] for k in values if k not in ("id", "created_at")},
                )
                await session.execute(stmt)

            stale = [did for did in existing if did not in new_ids]
            removed = 0
            if stale:
                result = await session.execute(
                    delete(AnfaDoctor).where(AnfaDoctor.id.in_(stale))
                )
                removed = result.rowcount or 0
            await session.commit()

        return {"added": added, "updated": updated, "removed": removed, "total": len(prepared)}


_repository: Optional[AnfaRepository] = None


def get_repository() -> AnfaRepository:
    global _repository
    if _repository is None:
        _repository = AnfaRepository()
    return _repository
