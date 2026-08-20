"""Embedders for vector search.

Two flavours ship with the platform:

  - `CLIPEmbedder`           — sentence-transformers CLIP for image+text search
                               (oygul: bouquet photos and English text queries)
  - `LiteLLMEmbeddingFunction` — text-only embeddings via LiteLLM, used as a
                               Chroma `EmbeddingFunction` (anfa: doctor/service
                               descriptions)
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import TYPE_CHECKING, Optional, Union

import httpx
from PIL import Image

if TYPE_CHECKING:
    from chromadb import Documents, EmbeddingFunction, Embeddings

logger = logging.getLogger(__name__)


class CLIPEmbedder:
    """Lazy singleton around a SentenceTransformer CLIP model.

    `clip-ViT-B-32` produces 512-dim vectors aligned across text and image, so
    a photo of a bouquet and the phrase "red roses" land near each other.
    Queries should be in English — CLIP is trained on English captions.
    """

    _instance: Optional["CLIPEmbedder"] = None

    def __init__(
        self,
        *,
        model_name: str = "clip-ViT-B-32",
        request_timeout: int = 15,
    ) -> None:
        from sentence_transformers import SentenceTransformer  # lazy import

        self._model = SentenceTransformer(model_name)
        self._timeout = request_timeout

    @classmethod
    def instance(
        cls,
        *,
        model_name: str = "clip-ViT-B-32",
        request_timeout: int = 15,
    ) -> "CLIPEmbedder":
        if cls._instance is None:
            cls._instance = cls(model_name=model_name, request_timeout=request_timeout)
        return cls._instance

    async def encode(self, query: Union[str, Image.Image]) -> list[float]:
        return await asyncio.to_thread(self._encode_sync, query)

    def _encode_sync(self, query: Union[str, Image.Image]) -> list[float]:
        vec = self._model.encode(query)
        return vec.tolist()

    async def load_image(self, source: Union[str, Image.Image, bytes]) -> Image.Image:
        if isinstance(source, Image.Image):
            return source
        if isinstance(source, bytes):
            return Image.open(BytesIO(source))
        if source.startswith("http://") or source.startswith("https://"):
            return await asyncio.to_thread(self._download_image, source)
        return Image.open(source)

    def _download_image(self, url: str) -> Image.Image:
        resp = httpx.get(url, timeout=self._timeout)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content))


class LiteLLMEmbeddingFunction:
    """Chroma-compatible embedding function backed by LiteLLM.

    Use as `Collection(..., embedding_function=LiteLLMEmbeddingFunction(...))`
    so that `collection.add(documents=[...])` and `collection.query(query_texts=[...])`
    automatically embed via LiteLLM.
    """

    def __init__(
        self,
        *,
        model: str = "openai/text-embedding-3-small",
        batch_size: int = 50,
    ) -> None:
        self._model = model
        self._batch_size = batch_size

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        import litellm  # lazy

        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            resp = litellm.embedding(model=self._model, input=batch)
            for item in resp["data"]:
                out.append(item["embedding"])
        return out

    # Chroma <=1.3 calls the EF directly; 1.4+ expects embed_documents /
    # embed_query (LangChain-style). Implement all three so we work across
    # versions.
    def __call__(self, input: "Documents") -> "Embeddings":  # noqa: A002
        return self._embed_batch(list(input))

    def embed_documents(self, input: "Documents") -> "Embeddings":  # noqa: A002
        return self._embed_batch(list(input))

    def embed_query(self, input):  # noqa: A002
        # Chroma 1.4 calls `embed_query(input=<sequence of strings>)` and
        # expects a *sequence of embeddings* back — not a single flat vector.
        # If we get a bare string instead, wrap it.
        if isinstance(input, str):
            return self._embed_batch([input])
        return self._embed_batch(list(input))

    def name(self) -> str:  # pragma: no cover - chroma plumbing
        return f"litellm:{self._model}"

    @staticmethod
    def build_from_config(config: dict) -> "LiteLLMEmbeddingFunction":  # pragma: no cover
        return LiteLLMEmbeddingFunction(
            model=config.get("model", "openai/text-embedding-3-small"),
            batch_size=int(config.get("batch_size", 50)),
        )

    def get_config(self) -> dict:  # pragma: no cover
        return {"model": self._model, "batch_size": self._batch_size}
