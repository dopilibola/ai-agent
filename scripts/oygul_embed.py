"""One-time bouquet ingestion: JSON → CLIP embeddings → ChromaDB.

Usage:
    uv run python scripts/oygul_embed.py --json bouquets.json
    uv run python scripts/oygul_embed.py --json bouquets.json --db data/oygul_chroma --batch 32

HNSW settings are immutable after collection creation, so this script always
deletes + recreates the collection to guarantee the configured settings apply.
`hnsw:search_ef=10000` is intentionally much larger than the dataset (~2500),
forcing HNSW to visit every node — effectively brute-force search.

Input JSON shape (one entry per bouquet):
    {
      "id": "...", "branch_id": "...", "name": "...", "description": "...",
      "tags": [...], "products_spent": [{"flower_name": "...", "quantity": 5}],
      "photo_url": "https://...", "price": 45000000, "created_at": "..."
    }
"""

from __future__ import annotations

import argparse
import json
from io import BytesIO

import chromadb
import httpx
from PIL import Image
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

CLIP_MODEL = "clip-ViT-B-32"
REQUEST_TIMEOUT = 15


def download_image(url: str) -> Image.Image | None:
    try:
        resp = httpx.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        print(f"  ⚠  Failed to download {url}: {e}")
        return None


def build_metadata(item: dict) -> dict:
    flowers = ",".join(p["flower_name"] for p in item.get("products_spent", []))
    return {
        "bouquet_id": item["id"],
        "branch_id": item["branch_id"],
        "name": item["name"],
        "description": item.get("description", ""),
        "tags": ",".join(item.get("tags", [])),
        "flowers": flowers,
        "price": int(item.get("price", 0)),
        "products_spent_json": json.dumps(
            item.get("products_spent", []), ensure_ascii=False
        ),
        "photo_url": item.get("photo_url", ""),
        "created_at": item.get("created_at", ""),
    }


def main(*, json_path: str, db_path: str, collection_name: str, batch_size: int) -> None:
    print(f"📂  Loading bouquets from {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        bouquets = json.load(f)
    print(f"    → {len(bouquets)} bouquets found")

    print(f"🤖  Loading CLIP model ({CLIP_MODEL})")
    model = SentenceTransformer(CLIP_MODEL)

    print(f"🗄️  Opening ChromaDB at {db_path}")
    client = chromadb.PersistentClient(path=db_path)
    if collection_name in [c.name for c in client.list_collections()]:
        print(f"   ↳ Deleting old '{collection_name}' to refresh HNSW settings")
        client.delete_collection(collection_name)

    collection = client.create_collection(
        name=collection_name,
        metadata={
            "hnsw:space": "cosine",
            "hnsw:construction_ef": 200,
            "hnsw:search_ef": 10000,
            "hnsw:M": 32,
        },
    )

    ids_batch, embeddings_batch, metadatas_batch = [], [], []
    all_metadatas: list[dict] = []  # for the Postgres source-of-truth mirror
    skipped = 0

    for item in tqdm(bouquets, desc="Embedding"):
        photo_url = item.get("photo_url", "")
        if not photo_url:
            print(f"\n  ⚠  No photo_url for id={item['id']}; skipping")
            skipped += 1
            continue
        image = download_image(photo_url)
        if image is None:
            skipped += 1
            continue
        embedding = model.encode(image, convert_to_numpy=True).tolist()
        meta = build_metadata(item)
        ids_batch.append(item["id"])
        embeddings_batch.append(embedding)
        metadatas_batch.append(meta)
        all_metadatas.append(meta)
        if len(ids_batch) >= batch_size:
            collection.upsert(
                ids=ids_batch, embeddings=embeddings_batch, metadatas=metadatas_batch
            )
            ids_batch.clear()
            embeddings_batch.clear()
            metadatas_batch.clear()

    if ids_batch:
        collection.upsert(
            ids=ids_batch, embeddings=embeddings_batch, metadatas=metadatas_batch
        )

    print(f"\n✅  Done. Stored {collection.count()} | Skipped {skipped}")
    print(f"   DB path    : {db_path}")
    print(f"   Collection : {collection_name}")

    # Mirror into the Postgres source of truth so the admin panel sees the
    # catalogue (Chroma is only the search index). Skipped if DATABASE_URL unset.
    _mirror_to_postgres(all_metadatas)


def _mirror_to_postgres(metadatas: list[dict]) -> None:
    import asyncio

    from db import database_configured

    if not database_configured():
        print("   ⚠  DATABASE_URL not set — skipped Postgres mirror (admin panel "
              "won't see these until you set it and run scripts/oygul_backfill_catalog.py).")
        return
    from apps.oygul.repository import get_repository

    written = asyncio.run(get_repository().upsert_bouquets_from_metadata(metadatas))
    print(f"   🗄  Mirrored {written} bouquets into Postgres (oygul_bouquets).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, help="Path to bouquets JSON file")
    parser.add_argument(
        "--db", default="data/oygul_chroma", help="ChromaDB persist directory"
    )
    parser.add_argument(
        "--collection", default="bouquets", help="Chroma collection name"
    )
    parser.add_argument("--batch", type=int, default=32, help="Upsert batch size")
    args = parser.parse_args()
    main(
        json_path=args.json,
        db_path=args.db,
        collection_name=args.collection,
        batch_size=args.batch,
    )
