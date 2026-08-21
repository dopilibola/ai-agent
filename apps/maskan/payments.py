"""Checkout links against the operator's **own** merchant account.

The Maskan Django backend can issue a Payme link of its own, but that link pays
*Maskan's* merchant account. This tenant sells on behalf of the operator running
the bot, so the money has to land in their account instead: the bot builds the
checkout links itself, from one invoice row (`maskan_payments`) per order, and
the providers then talk to `payments_api.py` about that row.

Two providers, two very different shapes:

* **Payme** — the link is a base64 of `m=<merchant>;ac.<field>=<invoice>;a=<tiyin>`,
  and everything else happens over the Merchant API (JSON-RPC) that Payme calls
  on us. `ac.<field>` must be spelled exactly as the merchant cabinet has it
  (`payme_account_field`), and its value is always a `maskan_payments.id`.
* **Uzum Bank** — payment starts from a deeplink carrying the merchant's
  `serviceId` plus the invoice, and Uzum then calls our check/create/confirm/
  reverse endpoints with Basic auth.

Amounts are in **tiyin** everywhere (1 so'm = 100 tiyin) — the unit both
providers speak. A provider with no credentials configured is simply absent from
the returned links, so a half-configured deploy still sells through the other
one instead of failing.
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

from apps.maskan.config import MaskanConfig, config as default_config
from apps.maskan.models import PROVIDER_PAYME, PROVIDER_UZUM

logger = logging.getLogger(__name__)

PAYME_CHECKOUT_TEST = "https://test.paycom.uz"
PAYME_CHECKOUT_PROD = "https://checkout.paycom.uz"

# Payme transaction states (Merchant API).
STATE_CREATED = 1
STATE_PERFORMED = 2
STATE_CANCELLED = -1
STATE_CANCELLED_AFTER_PERFORM = -2


def som_to_tiyin(amount_som) -> int:
    """So'm → tiyin. Accepts int/str/float; rounds to the nearest tiyin."""
    try:
        return int(round(float(amount_som) * 100))
    except (TypeError, ValueError):
        return 0


def tiyin_to_som(amount_tiyin: int) -> int:
    return int(amount_tiyin) // 100


def format_som(amount_tiyin: int) -> str:
    """`12 345 000 so'm` — thin spaces, the way prices are written in Uzbek."""
    return f"{tiyin_to_som(amount_tiyin):,}".replace(",", " ") + " so'm"


def payme_enabled(cfg: MaskanConfig = default_config) -> bool:
    return bool(cfg.payme_merchant_id)


def uzum_enabled(cfg: MaskanConfig = default_config) -> bool:
    return bool(cfg.uzum_service_id)


def any_provider_enabled(cfg: MaskanConfig = default_config) -> bool:
    return payme_enabled(cfg) or uzum_enabled(cfg)


def payme_checkout_url(
    payment_id: int,
    amount_tiyin: int,
    *,
    lang: str = "uz",
    cfg: MaskanConfig = default_config,
) -> str:
    """Payme's GET checkout: base64 of the `;`-separated parameter string.

    Padding is stripped because Payme's own examples carry none.
    """
    base = (PAYME_CHECKOUT_TEST if cfg.payme_test_mode else PAYME_CHECKOUT_PROD).rstrip("/")
    params = (
        f"m={cfg.payme_merchant_id};"
        f"ac.{cfg.payme_account_field}={int(payment_id)};"
        f"a={int(amount_tiyin)};"
        f"l={lang}"
    )
    encoded = base64.b64encode(params.encode("utf-8")).decode("utf-8").rstrip("=")
    return f"{base}/{encoded}"


def uzum_checkout_url(
    payment_id: int, amount_tiyin: int, *, cfg: MaskanConfig = default_config
) -> str:
    """Uzum Bank deeplink: serviceId + the invoice + the amount in tiyin."""
    base = cfg.uzum_checkout_url.rstrip("/")
    # The parameter name is not ours to choose: Uzum echoes back whatever field
    # the *service* declares in the merchant cabinet, and our callbacks look the
    # invoice up under that name. `order_id` matches how this merchant's
    # existing Uzum service is configured; override if the cabinet says
    # otherwise.
    return (
        f"{base}?serviceId={cfg.uzum_service_id}"
        f"&{cfg.uzum_param_field}={int(payment_id)}&amount={int(amount_tiyin)}"
    )


def build_links(
    payment_id: int, amount_tiyin: int, *, cfg: MaskanConfig = default_config
) -> dict[str, str]:
    """{provider: url} for every provider this deploy has credentials for."""
    links: dict[str, str] = {}
    if payme_enabled(cfg):
        links[PROVIDER_PAYME] = payme_checkout_url(payment_id, amount_tiyin, cfg=cfg)
    if uzum_enabled(cfg):
        links[PROVIDER_UZUM] = uzum_checkout_url(payment_id, amount_tiyin, cfg=cfg)
    return links


def payme_auth_ok(header: Optional[str], cfg: MaskanConfig = default_config) -> bool:
    """Validate Payme's `Authorization: Basic base64("Paycom:<merchant_key>")`.

    Rejects everything when no key is configured — an unauthenticated payment
    webhook is worse than a disabled one.
    """
    if not cfg.payme_merchant_key or not header:
        return False
    prefix, _, token = header.partition(" ")
    if prefix.lower() != "basic" or not token:
        return False
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return False
    login, _, password = decoded.partition(":")
    return login == "Paycom" and password == cfg.payme_merchant_key


def uzum_auth_ok(header: Optional[str], cfg: MaskanConfig = default_config) -> bool:
    """Validate Uzum's Basic auth (merchant login/password from the cabinet)."""
    if not cfg.uzum_merchant_login or not cfg.uzum_merchant_password or not header:
        return False
    prefix, _, token = header.partition(" ")
    if prefix.lower() != "basic" or not token:
        return False
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return False
    login, _, password = decoded.partition(":")
    return login == cfg.uzum_merchant_login and password == cfg.uzum_merchant_password
