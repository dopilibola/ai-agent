"""Manager tools — chat-driven administration of the anfa clinic data.

These back the MANAGER agent, which the bot serves to allow-listed clinic staff
(`ANFA_MANAGER_ALLOWED_IDS`, default = the moderator ids) instead of the
client-facing catalog advisor. They let staff manage three things over Telegram:

  - catalog PRICES (find a service → change its price / hide-show it)
  - the DOCTOR roster (add / edit / deactivate / delete)
  - MUTED chats (list, mute, unmute — the "AI off" flag operators toggle)

Every write goes straight to Postgres (the system of record for the runtime) and
then best-effort mirrors into Chroma so the change is searchable immediately; if
that live index fails (e.g. embeddings quota), the periodic KB sync still
reconciles it within `ANFA_SYNC_INTERVAL_SECONDS`.

CAVEAT worth knowing: the catalog and roster are also reconciled *whole* from the
clinic's Excel/Word exports on every import. A chat edit here is live at once, but
a later re-import of those source files overwrites rows that still exist in the
export (and re-adds deleted ones). For durable structural changes, edit the source
export too.
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from apps.anfa.import_doctors import parse_schedule
from apps.anfa.kb_index import (
    delete_ids,
    doc_id_doctor,
    doc_id_service,
    upsert_doctors,
    upsert_items,
)
from apps.anfa.repository import get_repository
from apps.anfa.services import get_mute_store, get_profile_store

logger = logging.getLogger(__name__)


async def _index_service(item) -> bool:
    """Best-effort immediate (re)index of one catalog row into the KB."""
    try:
        await upsert_items([item.to_kb_dict()])
        return True
    except Exception:
        logger.exception("Live KB index failed for service %s", item.id)
        return False


async def _index_doctor(doctor) -> bool:
    try:
        await upsert_doctors([doctor.to_kb_dict()])
        return True
    except Exception:
        logger.exception("Live KB index failed for doctor %s", doctor.id)
        return False


# ============================================================================
# Catalog prices
# ============================================================================


@tool
async def manager_find_services(query: str, limit: int = 10) -> str:
    """Search the service catalog by title or category to locate a row to edit.
    Returns each match's `id` (needed for price changes), tab, category, title,
    price (UZS), and whether it's active. Always call this first to get the
    exact `id` before changing a price or hiding a service."""
    items = await get_repository().find_items(query, limit=limit)
    return json.dumps(
        {
            "services": [
                {
                    "id": str(i.id),
                    "tab": i.tab,
                    "category": i.category,
                    "title": i.title,
                    "price": i.price,
                    "active": i.active,
                }
                for i in items
            ]
        },
        ensure_ascii=False,
    )


@tool
async def manager_set_service_price(item_id: str, price: int) -> str:
    """Set a service's price (whole UZS sum). Get `item_id` from
    `manager_find_services` first. `price` must be a non-negative integer (0 =
    price confirmed at the clinic). Only call after the staff member confirmed
    the exact service and the new number."""
    if price < 0:
        return json.dumps({"success": False, "error": "price must be >= 0"})
    item = await get_repository().set_item_price(item_id, int(price))
    if item is None:
        return json.dumps({"success": False, "error": "no service with that id"})
    searchable = await _index_service(item)
    return json.dumps(
        {
            "success": True,
            "id": str(item.id),
            "title": item.title,
            "price": item.price,
            "searchable_now": searchable,
        },
        ensure_ascii=False,
    )


@tool
async def manager_set_service_active(item_id: str, active: bool) -> str:
    """Show or hide a service. `active=false` removes it from client search
    (immediately pruned from the KB); `active=true` restores it. Get `item_id`
    from `manager_find_services` first."""
    repo = get_repository()
    ok = await repo.set_item_active(item_id, active)
    if not ok:
        return json.dumps({"success": False, "error": "no service with that id"})
    if active:
        item = await repo.get_item(item_id)
        searchable = await _index_service(item) if item else False
    else:
        await delete_ids([doc_id_service(item_id)])
        searchable = False
    return json.dumps(
        {"success": True, "id": str(item_id), "active": active, "searchable_now": searchable},
        ensure_ascii=False,
    )


# ============================================================================
# Doctor roster
# ============================================================================


@tool
async def manager_list_doctors(include_inactive: bool = False) -> str:
    """List the clinic's doctors. Returns each doctor's `id`, full name,
    speciality, experience, walk-in hours, and active flag. Use to find the
    `id` before editing, deactivating, or deleting a doctor."""
    docs = await get_repository().list_doctors(active_only=not include_inactive)
    return json.dumps(
        {
            "doctors": [
                {
                    "id": str(d.id),
                    "fullname": d.fullname,
                    "speciality": d.speciality,
                    "experience": d.experience,
                    "hours_label": d.hours_label,
                    "active": d.active,
                }
                for d in docs
            ]
        },
        ensure_ascii=False,
    )


@tool
async def manager_save_doctor(
    fullname: str,
    speciality: str,
    experience: str = "",
    hours: str = "",
) -> str:
    """Add a new doctor or update an existing one (matched by exact full name).

    fullname   : full name — the identity key. To edit a doctor, pass their
                 EXACT current name; a different name creates a new card (then
                 delete the old one).
    speciality : e.g. "Kardiolog" / "Ginekolog".
    experience : optional free text, e.g. "Oliy toifa, 12 yil".
    hours      : optional reception hours in free text, e.g.
                 "Du-Shan 09:00-14:00" or "Se, Pay 09:00-13:00" — it's parsed
                 into a weekly schedule + a clean label. Leave empty if unknown.

    Only call after the staff member confirmed the details. The card is live for
    clients right away."""
    fullname = fullname.strip()
    if not fullname or not speciality.strip():
        return json.dumps({"success": False, "error": "fullname and speciality are required"})
    schedule, hours_label = parse_schedule(hours) if hours.strip() else ({}, "")
    doctor = await get_repository().upsert_doctor(
        {
            "fullname": fullname,
            "speciality": speciality.strip(),
            "experience": experience.strip(),
            "hours_label": hours_label,
            "schedule": schedule,
        }
    )
    searchable = await _index_doctor(doctor)
    return json.dumps(
        {
            "success": True,
            "id": str(doctor.id),
            "fullname": doctor.fullname,
            "speciality": doctor.speciality,
            "hours_label": doctor.hours_label,
            "searchable_now": searchable,
        },
        ensure_ascii=False,
    )


@tool
async def manager_set_doctor_active(doctor_id: str, active: bool) -> str:
    """Deactivate (`active=false`) or reactivate a doctor. A deactivated doctor
    disappears from client search but is kept and can be restored — prefer this
    over deleting when someone is temporarily away. Get `doctor_id` from
    `manager_list_doctors`."""
    repo = get_repository()
    ok = await repo.set_doctor_active(doctor_id, active)
    if not ok:
        return json.dumps({"success": False, "error": "no doctor with that id"})
    if active:
        doc = await repo.get_doctor(doctor_id)
        searchable = await _index_doctor(doc) if doc else False
    else:
        await delete_ids([doc_id_doctor(doctor_id)])
        searchable = False
    return json.dumps(
        {"success": True, "id": str(doctor_id), "active": active, "searchable_now": searchable},
        ensure_ascii=False,
    )


@tool
async def manager_delete_doctor(doctor_id: str) -> str:
    """Permanently remove a doctor from the roster (e.g. they left the clinic).
    This deletes the row and prunes it from client search. Get `doctor_id` from
    `manager_list_doctors`, and only delete after the staff member confirmed —
    for a temporary absence use `manager_set_doctor_active` instead."""
    ok = await get_repository().delete_doctor(doctor_id)
    if not ok:
        return json.dumps({"success": False, "error": "no doctor with that id"})
    await delete_ids([doc_id_doctor(doctor_id)])
    return json.dumps({"success": True, "id": str(doctor_id), "deleted": True})


# ============================================================================
# Muted chats (the "AI off" flag)
# ============================================================================


@tool
async def manager_list_muted_chats() -> str:
    """List the chats where the bot is currently muted (a human is handling
    them). Returns each muted chat's `chat_id` and, if known, the client's name
    / @username. Use to see who's waiting on a person and to get a `chat_id` to
    unmute."""
    muted = await get_mute_store().snapshot()
    profiles = await get_profile_store().snapshot()
    chats = []
    for cid in sorted(muted):
        p = profiles.get(cid)
        chats.append(
            {
                "chat_id": cid,
                "name": getattr(p, "name", "") or "",
                "username": getattr(p, "username", "") or "",
            }
        )
    return json.dumps({"muted_chats": chats, "count": len(chats)}, ensure_ascii=False)


@tool
async def manager_set_chat_muted(chat_id: int, muted: bool) -> str:
    """Mute (`muted=true`) or unmute (`muted=false`) a specific chat by its
    numeric `chat_id`. Muting silences the bot so a human owns that chat;
    unmuting hands it back to the bot. Get chat ids from
    `manager_list_muted_chats` (or a handoff notification)."""
    store = get_mute_store()
    if muted:
        await store.mute(int(chat_id))
        changed = True
    else:
        changed = await store.unmute(int(chat_id))
    return json.dumps(
        {"success": True, "chat_id": int(chat_id), "muted": muted, "changed": changed}
    )


MANAGER_TOOLS = [
    manager_find_services,
    manager_set_service_price,
    manager_set_service_active,
    manager_list_doctors,
    manager_save_doctor,
    manager_set_doctor_active,
    manager_delete_doctor,
    manager_list_muted_chats,
    manager_set_chat_muted,
]
