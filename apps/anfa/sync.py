"""Knowledge-base sync: catalog rows + doctor roster (Postgres) → Chroma.

Incremental — only re-embeds rows whose content hash changed, and deletes ids
that disappeared. Runs on a short interval so an Excel/Word re-import from the
admin panel becomes searchable within minutes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from apps.anfa.config import AnfaConfig, config as default_config
from apps.anfa.repository import get_repository
from apps.anfa.kb_index import (
    DOCTOR_PREFIX,
    SERVICE_PREFIX,
    delete_ids,
    doctor_to_document,
    existing_hashes,
    item_to_document,
    upsert_doctors,
    upsert_items,
)

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeBaseSync:
    """Implements the `sync.SyncJob` protocol."""

    cfg: AnfaConfig = default_config
    name: ClassVar[str] = "anfa-kb-sync"

    @property
    def interval_seconds(self) -> int:
        return self.cfg.sync_interval_seconds

    async def run_once(self) -> None:
        logger.info("Syncing anfa catalog + doctors → KB…")
        repo = get_repository()
        items = [i.to_kb_dict() for i in await repo.list_catalog()]
        doctors = [d.to_kb_dict() for d in await repo.list_doctors()]

        existing = await existing_hashes()
        current_ids = {f"{SERVICE_PREFIX}{i['id']}" for i in items}
        current_ids |= {f"{DOCTOR_PREFIX}{d['id']}" for d in doctors}

        to_add_items = [
            i for i in items
            if _changed(existing, f"{SERVICE_PREFIX}{i['id']}", item_to_document(i)[1])
        ]
        to_add_doctors = [
            d for d in doctors
            if _changed(existing, f"{DOCTOR_PREFIX}{d['id']}", doctor_to_document(d)[1])
        ]

        to_delete = [doc_id for doc_id in existing if doc_id not in current_ids]
        if to_delete:
            logger.info("Deleting %d KB document(s) no longer present.", len(to_delete))
            await delete_ids(to_delete)
        if to_add_items:
            logger.info("Indexing %d catalog row(s).", len(to_add_items))
            await upsert_items(to_add_items)
        if to_add_doctors:
            logger.info("Indexing %d doctor(s).", len(to_add_doctors))
            await upsert_doctors(to_add_doctors)
        if not (to_delete or to_add_items or to_add_doctors):
            logger.info("KB already up to date.")
        logger.info(
            "Sync finished: %d catalog rows, %d doctors.", len(items), len(doctors)
        )


def _changed(existing: dict[str, str], doc_id: str, meta: dict) -> bool:
    return doc_id not in existing or existing[doc_id] != meta["content_hash"]
