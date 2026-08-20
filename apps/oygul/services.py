"""Singleton wiring of the per-tenant services needed by oygul's tools.

Kept dead simple: each `get_*()` function returns a module-level singleton
constructed lazily from the current `config`. Tests can monkey-patch the
module-level slots if they need to inject doubles.
"""

from __future__ import annotations

import logging
from typing import Optional, Union

from PIL import Image

from core import MuteStore, ProfileStore, TokenStore
from db import PostgresMuteStore, PostgresProfileStore, PostgresTokenStore
from notifications import TelegramOperatorNotifier
from rag import CLIPEmbedder, VectorStore
from voice import VoiceTranscriber
from apps.oygul.config import OygulConfig, config as default_config
from apps.oygul.models import Bouquet

logger = logging.getLogger(__name__)

_CHROMA_HNSW = {
    "hnsw:space": "cosine",
    "hnsw:construction_ef": 200,
    "hnsw:search_ef": 10000,
    "hnsw:M": 32,
}

# ---- voice ---------------------------------------------------------------

# Hint for Whisper — tells the model the domain and the languages it should
# expect on short clips. Uzbek first because that's the most common one
# misrecognised as a neighbouring Turkic language on tiny audio.
VOICE_PROMPT = (
    "Voice message from a customer in the OyGul flower shop Telegram chat in "
    "Tashkent, Uzbekistan. Customers speak Uzbek (Latin or Cyrillic) or "
    "Russian and sometimes mix the two within one message — transcribe in "
    "the language(s) actually spoken, do not translate. Likely vocabulary: "
    "guldasta, gul, atirgul, lola, piyon, tyulpan, yetkazib berish, do'kon, "
    "narx, so'm, Click, Payme; букет, доставка, цветы, розы, пионы, тюльпаны, "
    "сум."
)


_voice: Optional[VoiceTranscriber] = None


def get_voice(cfg: OygulConfig = default_config) -> VoiceTranscriber:
    global _voice
    if _voice is None:
        _voice = VoiceTranscriber(
            model=cfg.transcribe_model,
            prompt=VOICE_PROMPT,
            provider=cfg.transcribe_provider,
            api_key=cfg.google_api_key if cfg.transcribe_provider == "google_genai" else None,
        )
    return _voice


# ---- search --------------------------------------------------------------


class BouquetSearch:
    """CLIP-backed semantic search over the bouquet catalogue.

    Queries are CLIP embeddings of either text (English — CLIP is
    English-trained, Russian/Uzbek retrieves poorly) or a PIL Image.
    """

    def __init__(self, *, embedder: CLIPEmbedder, store: VectorStore) -> None:
        self._embedder = embedder
        self._store = store

    async def search(
        self,
        query: Union[str, Image.Image],
        *,
        flowers: Optional[list[str]] = None,
        price_gte: Optional[int] = None,
        price_lte: Optional[int] = None,
        top_k: int = 5,
        exclude_ids: Optional[set[str]] = None,
    ) -> list[Bouquet]:
        embedding = await self._embedder.encode(query)
        where = self._build_where(price_gte, price_lte)
        total = await self._store.count()
        # Over-fetch when post-filtering (by flower or by deactivated id) so the
        # excluded candidates don't leave us short of top_k.
        over_fetch = bool(flowers) or bool(exclude_ids)
        prefetch = min(top_k * 20 if over_fetch else top_k, total or top_k)
        result = await self._store.query_embedding(
            embedding, n_results=prefetch, where=where
        )
        metadatas_groups = result.get("metadatas") or [[]]
        metadatas = metadatas_groups[0] if metadatas_groups else []

        out: list[Bouquet] = []
        for meta in metadatas:
            bouquet = Bouquet.from_metadata(meta)
            if exclude_ids and bouquet.id in exclude_ids:
                continue
            if flowers and not bouquet.contains_flowers(flowers):
                continue
            out.append(bouquet)
            if len(out) == top_k:
                break
        return out

    async def index(
        self, *, bouquet_id: str, image: Image.Image, metadata: dict
    ) -> None:
        """CLIP-embed a bouquet image and upsert it into the catalogue
        collection. `metadata` must carry the keys `Bouquet.from_metadata`
        expects (bouquet_id, branch_id, name, description, tags, flowers, price
        in tiyin, products_spent_json, photo_url, created_at)."""
        embedding = await self._embedder.encode(image)
        await self._store.upsert(
            ids=[bouquet_id], embeddings=[embedding], metadatas=[metadata]
        )

    @staticmethod
    def _build_where(
        price_gte: Optional[int], price_lte: Optional[int]
    ) -> Optional[dict]:
        clauses: list[dict] = []
        # Prices are stored in tiyin; user-supplied bounds are in sum.
        if price_gte is not None:
            clauses.append({"price": {"$gte": price_gte * 100}})
        if price_lte is not None:
            clauses.append({"price": {"$lte": price_lte * 100}})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}


_search: Optional[BouquetSearch] = None


def get_search(cfg: OygulConfig = default_config) -> BouquetSearch:
    global _search
    if _search is None:
        embedder = CLIPEmbedder.instance(
            model_name=cfg.clip_model, request_timeout=cfg.request_timeout
        )
        store = VectorStore(
            db_path=cfg.chroma_path,
            collection_name=cfg.collection_name,
            collection_metadata=_CHROMA_HNSW,
        )
        _search = BouquetSearch(embedder=embedder, store=store)
    return _search


# ---- operator notifier ---------------------------------------------------

_notifier: Optional[TelegramOperatorNotifier] = None


def get_notifier(cfg: OygulConfig = default_config) -> TelegramOperatorNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramOperatorNotifier(
            bot_token=cfg.merchant_bot_token,
            admin_chat_ids=cfg.operator_chat_ids,
            request_timeout=cfg.request_timeout,
        )
    return _notifier


# ---- AI mute store -------------------------------------------------------

_mute_store: Optional[MuteStore] = None


def get_mute_store(cfg: OygulConfig = default_config) -> MuteStore:
    """Per-tenant set of chat_ids where Lola is currently muted (a human
    operator has taken over). Shared across the customer userbot and merchant
    bot processes via Postgres."""
    global _mute_store
    if _mute_store is None:
        _mute_store = PostgresMuteStore("oygul")
    return _mute_store


_token_store: Optional[TokenStore] = None


def get_token_store(cfg: OygulConfig = default_config) -> TokenStore:
    """Per-tenant ledger of token usage per chat — current run + cumulative."""
    global _token_store
    if _token_store is None:
        _token_store = PostgresTokenStore("oygul")
    return _token_store


_profile_store: Optional[ProfileStore] = None


def get_profile_store(cfg: OygulConfig = default_config) -> ProfileStore:
    """Per-tenant cache of each chat's Telegram name/@username, captured by the
    channels so the admin panel can label chats by who they are."""
    global _profile_store
    if _profile_store is None:
        _profile_store = PostgresProfileStore("oygul")
    return _profile_store
