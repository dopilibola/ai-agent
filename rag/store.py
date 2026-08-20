"""VectorStore — tenant-scoped ChromaDB collection wrapper.

Each tenant owns one collection (chosen by name) backed by a path on disk.
The store doesn't care whether you embed externally (CLIP — pass vectors) or
let it embed for you (LiteLLM — set `embedding_function` and pass texts).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class VectorStore:
    """Async-friendly facade over a ChromaDB persistent collection."""

    def __init__(
        self,
        *,
        db_path: str,
        collection_name: str,
        embedding_function: Optional[Any] = None,
        collection_metadata: Optional[dict] = None,
    ) -> None:
        import chromadb  # lazy

        self._client = chromadb.PersistentClient(path=db_path)
        kwargs: dict[str, Any] = {"name": collection_name}
        if embedding_function is not None:
            kwargs["embedding_function"] = embedding_function
        if collection_metadata is not None:
            kwargs["metadata"] = collection_metadata
        self._collection = self._client.get_or_create_collection(**kwargs)

    @property
    def collection(self) -> Any:
        """Direct handle for callers that need Chroma-specific operations."""
        return self._collection

    async def count(self) -> int:
        return await asyncio.to_thread(self._collection.count)

    async def upsert(
        self,
        *,
        ids: list[str],
        documents: Optional[list[str]] = None,
        embeddings: Optional[list[list[float]]] = None,
        metadatas: Optional[list[dict]] = None,
    ) -> None:
        await asyncio.to_thread(
            self._collection.upsert,
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    async def delete(self, ids: list[str]) -> None:
        await asyncio.to_thread(self._collection.delete, ids=ids)

    async def query_embedding(
        self,
        embedding: list[float],
        *,
        n_results: int = 10,
        where: Optional[dict] = None,
    ) -> dict:
        kwargs: dict[str, Any] = {
            "query_embeddings": [embedding],
            "n_results": n_results,
        }
        if where:
            kwargs["where"] = where
        return await asyncio.to_thread(self._collection.query, **kwargs)

    async def query_text(
        self,
        text: str,
        *,
        n_results: int = 10,
        where: Optional[dict] = None,
    ) -> dict:
        kwargs: dict[str, Any] = {
            "query_texts": [text],
            "n_results": n_results,
        }
        if where:
            kwargs["where"] = where
        return await asyncio.to_thread(self._collection.query, **kwargs)

    async def all_metadata(self) -> dict:
        """Return every doc id + metadata (for incremental sync diffs)."""
        return await asyncio.to_thread(self._collection.get, include=["metadatas"])
