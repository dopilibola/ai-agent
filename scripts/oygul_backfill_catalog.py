"""Backfill the Postgres `oygul_bouquets` table from the existing Chroma catalogue.

The bulk seed (`oygul_embed.py`) historically wrote only Chroma (the search
index), so the admin panel — which reads the Postgres source of truth — showed
an empty catalogue even though the agent could search bouquets. This reads every
bouquet's Chroma metadata and upserts the matching `oygul_bouquets` row.

Idempotent (upsert by id); existing `created_at` is preserved. Requires
DATABASE_URL. Usage:  uv run python scripts/oygul_backfill_catalog.py
"""

from __future__ import annotations

import asyncio

from apps.oygul.config import config
from apps.oygul.repository import get_repository
from db import database_configured
from rag import VectorStore


async def _backfill() -> None:
    if not database_configured():
        raise SystemExit("DATABASE_URL is not set; nothing to back-fill.")

    store = VectorStore(db_path=config.chroma_path, collection_name=config.collection_name)
    result = store.collection.get(include=["metadatas"], limit=1_000_000)
    metadatas = result.get("metadatas") or []
    print(f"Found {len(metadatas)} bouquets in Chroma ({config.collection_name}).")

    written = await get_repository().upsert_bouquets_from_metadata(metadatas)
    print(f"✅ Backfilled {written} bouquets into Postgres.")


def run() -> None:
    asyncio.run(_backfill())


if __name__ == "__main__":
    run()
