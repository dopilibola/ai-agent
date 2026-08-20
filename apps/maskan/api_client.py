"""HTTP client for Maskan's Django backend (`/api/bot/...`).

The backend owns the catalogue, the graves, the orders and the money. This
module is the only place in the tenant that talks to it, so every tool and every
funnel executor sees one consistent view — and one consistent failure mode.

Design notes:

* **Auth is a shared secret**, not a user token: the header `X-Bot-Key` plus a
  Telegram `chat_id` in the query/body. The backend resolves the chat to a
  Maskan account via `accounts.User.telegram_chat_id`, which the existing
  @Maskanuzbot `/start` flow already populates.
* **Reference data is cached** in-process for `api_cache_seconds` — services and
  cemeteries change rarely, and an agent that asks "what do we offer" three
  times in one reply shouldn't produce three round trips.
* **Errors are values, not exceptions.** Every method returns a plain
  dict/list, and `ApiError` is raised only for genuinely broken calls; tools
  catch it and hand the model a readable message instead of a traceback, so a
  backend hiccup degrades the conversation rather than killing the turn.
* `httpx.AsyncClient` is a lazy module-level singleton (connection reuse), the
  same shape as the other lazy service singletons in the platform.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from apps.maskan.config import MaskanConfig, config as default_config

logger = logging.getLogger(__name__)


class ApiError(RuntimeError):
    """A bot-API call that could not be completed (network, auth, 5xx, or 4xx).

    `detail` carries the backend's human-readable message when it sent one —
    tools surface it to the model, which relays the gist to the client.
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.detail = message
        self.status_code = status_code


_client: Optional[httpx.AsyncClient] = None


def _http(cfg: MaskanConfig) -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=f"{cfg.api_base}/api/bot",
            timeout=cfg.api_timeout,
            headers={"X-Bot-Key": cfg.api_key, "Accept": "application/json"},
        )
    return _client


async def aclose() -> None:
    """Close the shared client (called on shutdown; safe to call twice)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ----- reference-data cache -------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = asyncio.Lock()

# Cemetery lookups are keyed by the client's own wording, so the key space is
# effectively unbounded over a long-running process. Cap it and drop the oldest
# entries when it fills — this is a latency cache, not a store.
_CACHE_MAX_ENTRIES = 256


def invalidate_cache() -> None:
    _cache.clear()


async def _cached(key: str, loader, cfg: MaskanConfig):
    """Fetch through the TTL cache. The lock keeps a burst of tool calls in one
    turn from firing N identical requests before the first one lands."""
    ttl = cfg.api_cache_seconds
    hit = _cache.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < ttl:
        return hit[1]
    async with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and (time.monotonic() - hit[0]) < ttl:
            return hit[1]
        value = await loader()
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            # dicts keep insertion order, so the first keys are the oldest.
            for stale in list(_cache)[: len(_cache) - _CACHE_MAX_ENTRIES + 1]:
                _cache.pop(stale, None)
        _cache[key] = (time.monotonic(), value)
        return value


# ----- low-level request ----------------------------------------------------

async def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
    cfg: MaskanConfig = default_config,
) -> Any:
    if not cfg.api_configured:
        raise ApiError(
            "Maskan backend sozlanmagan: MASKAN_API_BASE va MASKAN_API_KEY ni .env ga qo'shing."
        )
    try:
        response = await _http(cfg).request(method, path, params=params, json=json)
    except httpx.RequestError as exc:
        logger.warning("Maskan API %s %s unreachable: %s", method, path, exc)
        raise ApiError("Maskan serveriga ulanib bo'lmadi.") from exc

    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("detail") or "")
                if not detail:
                    # DRF field errors: {"grave": ["Majburiy."]}
                    detail = "; ".join(
                        f"{k}: {v[0] if isinstance(v, list) and v else v}"
                        for k, v in body.items()
                    )
        except ValueError:
            detail = ""
        logger.warning(
            "Maskan API %s %s → %s %s", method, path, response.status_code, detail
        )
        raise ApiError(
            detail or f"Maskan serveri xato qaytardi (HTTP {response.status_code}).",
            status_code=response.status_code,
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ApiError("Maskan serveri kutilmagan javob qaytardi.") from exc


# ----- reference data -------------------------------------------------------

async def ping(cfg: MaskanConfig = default_config) -> bool:
    """Startup probe — logs a clear warning instead of failing a live chat."""
    try:
        await _request("GET", "/ping/", cfg=cfg)
        return True
    except ApiError as exc:
        logger.warning("Maskan backend probe failed: %s", exc.detail)
        return False


async def list_services(cfg: MaskanConfig = default_config) -> list[dict]:
    """The standard price list, cheapest first."""

    async def _load():
        data = await _request("GET", "/services/", cfg=cfg)
        return list(data.get("results") or [])

    return await _cached("services", _load, cfg)


async def find_cemeteries(
    query: str = "", *, city_id: Optional[int] = None, cfg: MaskanConfig = default_config
) -> list[dict]:
    """Search cemeteries by name, city or region.

    Cached per (query, city) because clients often re-ask with the same wording.
    """
    params: dict[str, Any] = {}
    if query:
        params["q"] = query
    if city_id:
        params["city"] = city_id

    async def _load():
        data = await _request("GET", "/cemeteries/", params=params, cfg=cfg)
        return list(data.get("results") or [])

    return await _cached(f"cemeteries:{query}:{city_id}", _load, cfg)


# ----- account --------------------------------------------------------------

async def resolve_user(chat_id: int, cfg: MaskanConfig = default_config) -> Optional[dict]:
    """The Maskan account linked to this Telegram chat, or None if unlinked.

    Never cached: linking happens mid-conversation and a stale "not linked"
    would strand the client in the onboarding branch.
    """
    data = await _request("GET", "/users/resolve/", params={"chat_id": chat_id}, cfg=cfg)
    return data.get("user") if data.get("found") else None


# There is deliberately no `link_account` here. Linking a chat to an account by
# a typed-in phone number would be an account-takeover path: `telegram_chat_id`
# is the channel Maskan sends **password-reset codes** to, so anyone who knew a
# phone number could redirect a stranger's codes to their own chat. Linking
# stays in the existing @Maskanuzbot flow, which proves phone ownership with
# Telegram's own "share contact" button; here we only ever *read* the link
# state via `resolve_user`.


# ----- graves ---------------------------------------------------------------

async def list_graves(chat_id: int, cfg: MaskanConfig = default_config) -> list[dict]:
    data = await _request("GET", "/graves/", params={"chat_id": chat_id}, cfg=cfg)
    return list(data.get("results") or [])


async def add_grave(
    chat_id: int,
    *,
    cemetery_id: int,
    name: str,
    relation: str = "",
    born: Optional[int] = None,
    died: Optional[int] = None,
    sector: str = "",
    cfg: MaskanConfig = default_config,
) -> dict:
    """Register a grave for this client. Idempotent on (cemetery, full name)."""
    payload = {
        "chat_id": chat_id,
        "cemetery": cemetery_id,
        "name": name,
        "rel_uz": relation,
        "born": born,
        "died": died,
        "sector": sector,
    }
    data = await _request("POST", "/graves/", json=payload, cfg=cfg)
    return data.get("grave") or {}


# ----- orders ---------------------------------------------------------------

async def create_order(
    chat_id: int,
    *,
    grave_id: int,
    service_codes: list[str],
    frequency: str = "once",
    cfg: MaskanConfig = default_config,
) -> dict:
    """Create an awaiting-payment order and get its Payme checkout URL.

    Prices are resolved server-side from the service codes, so a model that
    misremembers a price cannot put a wrong amount into a real order.
    """
    payload = {
        "chat_id": chat_id,
        "grave": grave_id,
        "services": service_codes,
        "frequency": frequency,
    }
    return await _request("POST", "/orders/init/", json=payload, cfg=cfg)


async def list_orders(chat_id: int, cfg: MaskanConfig = default_config) -> list[dict]:
    data = await _request("GET", "/orders/", params={"chat_id": chat_id}, cfg=cfg)
    return list(data.get("results") or [])


async def order_status(
    order_ids: list[int], cfg: MaskanConfig = default_config
) -> list[dict]:
    """Status-only rows for the ids the watcher is tracking (no items)."""
    ids = [int(i) for i in order_ids if i]
    if not ids:
        return []
    data = await _request(
        "GET", "/orders/status/", params={"ids": ",".join(str(i) for i in ids)}, cfg=cfg
    )
    return list(data.get("results") or [])
