"""Document shape + indexing helpers for the clinic knowledge base.

A single Chroma collection holds two doc types — the priced service catalog
(`service_*`) and the doctor roster (`doctor_*`) — embedded with LiteLLM text
embeddings (multilingual, so Uzbek queries match the Russian catalog and the
Uzbek roster). The KB is queried by the agent (`search_services` /
`search_doctors` tools) and refreshed from Postgres by the sync job.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Iterable, Optional

from rag import LiteLLMEmbeddingFunction, VectorStore
from apps.anfa.config import AnfaConfig, config as default_config

logger = logging.getLogger(__name__)

SERVICE_PREFIX = "service_"
DOCTOR_PREFIX = "doctor_"


def doc_id_service(service_id: str | int) -> str:
    return f"{SERVICE_PREFIX}{service_id}"


def doc_id_doctor(doctor_id: str | int) -> str:
    return f"{DOCTOR_PREFIX}{doctor_id}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def item_to_document(item: dict) -> tuple[str, dict]:
    """Build the embedded text + Chroma metadata for one catalog row.

    The embedded text is title + category + tab so a query like "детский
    невролог" or "qon tahlili" lands near the right rows. Price stays in
    metadata only — it must not skew the semantic match."""
    title = (item.get("title") or "").strip()
    category = (item.get("category") or "").strip()
    tab = (item.get("tab") or "").strip()
    text = " ".join(filter(None, [title, category, tab]))
    meta = {
        "type": "service",
        "id": str(item["id"]),
        "title": title,
        "category": category,
        "tab": tab,
        "price": int(item.get("price") or 0),
        "currency": (item.get("currency") or "UZS"),
        "content_hash": _content_hash(text + f"|{int(item.get('price') or 0)}"),
    }
    return text, meta


def doctor_to_document(doctor: dict) -> tuple[str, dict]:
    """Build the embedded text + Chroma metadata for one doctor card.

    Embedded text is fullname + speciality + experience so a query like
    "кардиолог" / "yurak shifokori" / a doctor's name lands on the card.
    Walk-in hours stay in metadata (shown, not matched on)."""
    fullname = (doctor.get("fullname") or "").strip()
    speciality = (doctor.get("speciality") or "").strip()
    experience = (doctor.get("experience") or "").strip()
    hours_label = (doctor.get("hours_label") or "").strip()
    text = " ".join(filter(None, [fullname, speciality, experience]))
    meta = {
        "type": "doctor",
        "id": str(doctor["id"]),
        "fullname": fullname,
        "speciality": speciality,
        "experience": experience,
        "hours_label": hours_label,
        "content_hash": _content_hash(text + "|" + hours_label),
    }
    return text, meta


def _normalise_metadata(metadatas: Iterable[dict]) -> None:
    """Chroma metadata values must be str/int/float/bool. Coerce in place."""
    for m in metadatas:
        for k, v in list(m.items()):
            if v is None:
                m[k] = ""
            elif isinstance(v, (int, float, bool, str)):
                continue
            else:
                m[k] = str(v)


# ----- store singleton --------------------------------------------------

_store: Optional[VectorStore] = None


def get_store(cfg: AnfaConfig = default_config) -> VectorStore:
    global _store
    if _store is None:
        ef = LiteLLMEmbeddingFunction(
            model=cfg.embed_model, batch_size=cfg.embed_batch_size
        )
        _store = VectorStore(
            db_path=cfg.chroma_path,
            collection_name=cfg.collection_name,
            embedding_function=ef,
        )
    return _store


# ----- index operations -------------------------------------------------


async def upsert_items(items: list[dict]) -> None:
    if not items:
        return
    pairs = [item_to_document(s) for s in items]
    ids = [doc_id_service(s["id"]) for s in items]
    docs = [p[0] for p in pairs]
    metas = [p[1] for p in pairs]
    _normalise_metadata(metas)
    await get_store().upsert(ids=ids, documents=docs, metadatas=metas)


async def upsert_doctors(doctors: list[dict]) -> None:
    if not doctors:
        return
    pairs = [doctor_to_document(d) for d in doctors]
    ids = [doc_id_doctor(d["id"]) for d in doctors]
    docs = [p[0] for p in pairs]
    metas = [p[1] for p in pairs]
    _normalise_metadata(metas)
    await get_store().upsert(ids=ids, documents=docs, metadatas=metas)


async def delete_ids(ids: list[str]) -> None:
    if ids:
        await get_store().delete(ids)


async def existing_hashes() -> dict[str, str]:
    """Return doc_id → content_hash for every document (for incremental sync)."""
    out: dict[str, str] = {}
    coll = get_store().collection
    result = await asyncio.to_thread(coll.get, include=["metadatas"], limit=100_000)
    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    for i, doc_id in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else {}
        h = meta.get("content_hash") if meta else None
        if h:
            out[doc_id] = h
    return out
