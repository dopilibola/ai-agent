"""Cloudflare Images upload — durable public hosting for bouquet photos.

The merchant uploads a photo into the bot chat; `add_bouquet_tool` sends the
raw bytes here to get back a stable HTTPS delivery URL (`imagedelivery.net/...`)
that gets stored on the bouquet row + Chroma metadata. Telegram fetches that URL
when Lola sends the customer album, so it must be publicly reachable.

API: POST multipart `file` to
`https://api.cloudflare.com/client/v4/accounts/{account}/images/v1` with a
Bearer token; the response's `result.variants` holds the delivery URLs.
"""

from __future__ import annotations

import logging

import httpx

from apps.oygul.config import OygulConfig, config as default_config

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.cloudflare.com/client/v4"


class PhotoUploadError(RuntimeError):
    """Raised when the image host rejects an upload or isn't configured."""


def _pick_variant(variants: list[str], preferred: str) -> str:
    """Choose the delivery URL whose variant matches `preferred` (e.g. /public),
    falling back to the first one Cloudflare returned."""
    for url in variants:
        if url.rstrip("/").rsplit("/", 1)[-1] == preferred:
            return url
    return variants[0]


async def upload_image(
    image_bytes: bytes,
    *,
    filename: str = "bouquet.jpg",
    cfg: OygulConfig = default_config,
) -> str:
    """Upload `image_bytes` to Cloudflare Images, returning a public delivery URL.

    Raises `PhotoUploadError` if CF isn't configured or the upload fails.
    """
    if not cfg.cf_account_id or not cfg.cf_images_token:
        raise PhotoUploadError(
            "Cloudflare Images is not configured "
            "(set OYGUL_CF_ACCOUNT_ID + OYGUL_CF_IMAGES_TOKEN)."
        )
    url = f"{_API_ROOT}/accounts/{cfg.cf_account_id}/images/v1"
    try:
        async with httpx.AsyncClient(timeout=cfg.request_timeout) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {cfg.cf_images_token}"},
                files={"file": (filename, image_bytes, "image/jpeg")},
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # network error, non-2xx, or bad JSON
        raise PhotoUploadError(f"Cloudflare upload failed: {exc}") from exc

    if not data.get("success"):
        raise PhotoUploadError(f"Cloudflare upload rejected: {data.get('errors')}")
    variants = (data.get("result") or {}).get("variants") or []
    if not variants:
        raise PhotoUploadError("Cloudflare upload returned no delivery URL.")
    return _pick_variant(variants, cfg.cf_images_variant)
