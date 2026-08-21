"""Payment callbacks — the endpoint Payme and Uzum call about our invoices.

Runs as its own tiny FastAPI process (`maskan-payments-api`), behind nginx+TLS,
because both providers require a public HTTPS callback and neither of them
speaks Telegram. It does exactly one thing: move a `maskan_payments` row through
its lifecycle. It never sends a message — `payment_watcher.py`, inside the bot
process where the Telethon client lives, picks up rows marked paid and does the
talking. That split is why a payment recorded here still reaches the client if
the bot happens to be restarting.

Payme speaks the Merchant API (JSON-RPC 2.0):
CheckPerformTransaction → CreateTransaction → PerformTransaction, plus
CancelTransaction / CheckTransaction / GetStatement. Auth is
`Basic base64("Paycom:<merchant_key>")`. The account value in a checkout link is
a `maskan_payments.id`.

Uzum Bank calls plain JSON endpoints (check / create / confirm / reverse) with
the merchant's Basic credentials. **Verify the exact field names against your
Uzum merchant cabinet before going live** — the shapes below are permissive on
input (they accept the id under several common keys) and conservative on output.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

# Imported at module scope on purpose: with `from __future__ import annotations`
# FastAPI resolves a handler's type hints against the *module* globals, so a
# `Request` imported inside create_app() would be invisible to it and every
# body parameter would silently become a query parameter.
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from apps.maskan import payments
from apps.maskan.config import config
from apps.maskan.models import (
    PAYMENT_CANCELLED,
    PAYMENT_CREATED,
    PAYMENT_NEW,
    PAYMENT_PAID,
    PROVIDER_PAYME,
    PROVIDER_UZUM,
)
from apps.maskan.repository import get_repository

logger = logging.getLogger(__name__)

# ----- Payme error codes (protocol-defined) ---------------------------------
ERR_TRANSPORT = -32300
ERR_AUTH = -32504
ERR_METHOD = -32601
ERR_WRONG_AMOUNT = -31001
ERR_TXN_NOT_FOUND = -31003
ERR_CANNOT_PERFORM = -31008
ERR_ACCOUNT = -31050  # "account not found" range is -31050…-31099

_TXN_TIMEOUT_MS = 12 * 60 * 60 * 1000  # Payme cancels a stale transaction after 12h


def _now_ms() -> int:
    return int(time.time() * 1000)


def _err(code: int, message: str, data: Optional[str] = None) -> dict:
    """A Payme error object; `message` is returned in all three locales it wants."""
    error: dict[str, Any] = {
        "code": code,
        "message": {"uz": message, "ru": message, "en": message},
    }
    if data:
        error["data"] = data
    return {"error": error}


def _account_id(params: dict) -> Optional[int]:
    """Pull our invoice id out of Payme's `account` object.

    Accepts the configured field name first, then any single value present —
    a cabinet whose field is spelled differently than expected still works
    rather than silently rejecting every payment.
    """
    account = params.get("account") or {}
    if not isinstance(account, dict):
        return None
    raw = account.get(config.payme_account_field)
    if raw is None:
        values = [v for v in account.values() if v not in (None, "")]
        raw = values[0] if len(values) == 1 else None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


async def _payme_check_perform(params: dict) -> dict:
    payment_id = _account_id(params)
    if payment_id is None:
        return _err(ERR_ACCOUNT, "Buyurtma raqami noto'g'ri", config.payme_account_field)
    payment = await get_repository().get_payment(payment_id)
    if payment is None:
        return _err(ERR_ACCOUNT, "Buyurtma topilmadi", config.payme_account_field)
    if payment.state == PAYMENT_PAID:
        return _err(ERR_CANNOT_PERFORM, "Buyurtma allaqachon to'langan")
    if int(params.get("amount") or 0) != int(payment.amount_tiyin):
        return _err(ERR_WRONG_AMOUNT, "Summa noto'g'ri")
    return {"result": {"allow": True}}


async def _payme_create(params: dict) -> dict:
    txn_id = str(params.get("id") or "")
    repo = get_repository()
    existing = await repo.get_payment_by_txn(PROVIDER_PAYME, txn_id)
    if existing is not None:
        # Payme retries CreateTransaction; the answer must be identical.
        if existing.state in (PAYMENT_CREATED, PAYMENT_PAID):
            return {
                "result": {
                    "create_time": existing.create_time,
                    "transaction": str(existing.id),
                    "state": payments.STATE_CREATED
                    if existing.state == PAYMENT_CREATED
                    else payments.STATE_PERFORMED,
                }
            }
        return _err(ERR_CANNOT_PERFORM, "Tranzaksiya bekor qilingan")

    check = await _payme_check_perform(params)
    if "error" in check:
        return check
    payment_id = _account_id(params)
    payment = await get_repository().get_payment(payment_id)
    if payment is None:
        return _err(ERR_ACCOUNT, "Buyurtma topilmadi", config.payme_account_field)
    if payment.provider_txn_id and payment.provider_txn_id != txn_id:
        # Another transaction already owns this invoice.
        return _err(ERR_CANNOT_PERFORM, "Bu buyurtma uchun boshqa tranzaksiya ochilgan")

    create_time = int(params.get("time") or _now_ms())
    payment = await repo.update_payment(
        payment.id,
        provider=PROVIDER_PAYME,
        provider_txn_id=txn_id,
        state=PAYMENT_CREATED,
        create_time=create_time,
    )
    return {
        "result": {
            "create_time": create_time,
            "transaction": str(payment.id),
            "state": payments.STATE_CREATED,
        }
    }


async def _payme_perform(params: dict) -> dict:
    txn_id = str(params.get("id") or "")
    repo = get_repository()
    payment = await repo.get_payment_by_txn(PROVIDER_PAYME, txn_id)
    if payment is None:
        return _err(ERR_TXN_NOT_FOUND, "Tranzaksiya topilmadi")
    if payment.state == PAYMENT_PAID:
        return {
            "result": {
                "transaction": str(payment.id),
                "perform_time": payment.perform_time,
                "state": payments.STATE_PERFORMED,
            }
        }
    if payment.state != PAYMENT_CREATED:
        return _err(ERR_CANNOT_PERFORM, "Tranzaksiya holati mos emas")
    if payment.create_time and _now_ms() - payment.create_time > _TXN_TIMEOUT_MS:
        await repo.update_payment(
            payment.id,
            state=PAYMENT_CANCELLED,
            cancel_time=_now_ms(),
            reason=4,  # Payme: timeout
        )
        return _err(ERR_CANNOT_PERFORM, "Tranzaksiya muddati o'tgan")

    perform_time = _now_ms()
    payment = await repo.update_payment(
        payment.id, state=PAYMENT_PAID, perform_time=perform_time
    )
    logger.info("Payme payment %s performed (%s tiyin)", payment.id, payment.amount_tiyin)
    return {
        "result": {
            "transaction": str(payment.id),
            "perform_time": perform_time,
            "state": payments.STATE_PERFORMED,
        }
    }


async def _payme_cancel(params: dict) -> dict:
    txn_id = str(params.get("id") or "")
    repo = get_repository()
    payment = await repo.get_payment_by_txn(PROVIDER_PAYME, txn_id)
    if payment is None:
        return _err(ERR_TXN_NOT_FOUND, "Tranzaksiya topilmadi")
    was_paid = payment.state == PAYMENT_PAID
    cancel_time = payment.cancel_time or _now_ms()
    if payment.state != PAYMENT_CANCELLED:
        payment = await repo.update_payment(
            payment.id,
            state=PAYMENT_CANCELLED,
            cancel_time=cancel_time,
            reason=int(params.get("reason") or 0) or None,
        )
    return {
        "result": {
            "transaction": str(payment.id),
            "cancel_time": cancel_time,
            "state": payments.STATE_CANCELLED_AFTER_PERFORM
            if was_paid
            else payments.STATE_CANCELLED,
        }
    }


async def _payme_check(params: dict) -> dict:
    txn_id = str(params.get("id") or "")
    payment = await get_repository().get_payment_by_txn(PROVIDER_PAYME, txn_id)
    if payment is None:
        return _err(ERR_TXN_NOT_FOUND, "Tranzaksiya topilmadi")
    state = {
        PAYMENT_CREATED: payments.STATE_CREATED,
        PAYMENT_PAID: payments.STATE_PERFORMED,
        PAYMENT_CANCELLED: payments.STATE_CANCELLED_AFTER_PERFORM
        if payment.perform_time
        else payments.STATE_CANCELLED,
    }.get(payment.state, payments.STATE_CREATED)
    return {
        "result": {
            "create_time": payment.create_time,
            "perform_time": payment.perform_time,
            "cancel_time": payment.cancel_time,
            "transaction": str(payment.id),
            "state": state,
            "reason": payment.reason,
        }
    }


async def _payme_statement(params: dict) -> dict:
    """Payme reconciles its ledger against ours over a time window."""
    from apps.maskan.models import MaskanPayment  # local: keeps the module light
    from sqlalchemy import select

    from db.engine import get_sessionmaker

    start = int(params.get("from") or 0)
    end = int(params.get("to") or _now_ms())
    async with get_sessionmaker()() as session:
        rows = list(
            await session.scalars(
                select(MaskanPayment).where(
                    MaskanPayment.provider == PROVIDER_PAYME,
                    MaskanPayment.create_time >= start,
                    MaskanPayment.create_time <= end,
                )
            )
        )
    return {
        "result": {
            "transactions": [
                {
                    "id": row.provider_txn_id,
                    "time": row.create_time,
                    "amount": row.amount_tiyin,
                    "account": {config.payme_account_field: str(row.id)},
                    "create_time": row.create_time,
                    "perform_time": row.perform_time,
                    "cancel_time": row.cancel_time,
                    "transaction": str(row.id),
                    "state": payments.STATE_PERFORMED
                    if row.state == PAYMENT_PAID
                    else payments.STATE_CREATED,
                    "reason": row.reason,
                }
                for row in rows
            ]
        }
    }


_PAYME_METHODS = {
    "CheckPerformTransaction": _payme_check_perform,
    "CreateTransaction": _payme_create,
    "PerformTransaction": _payme_perform,
    "CancelTransaction": _payme_cancel,
    "CheckTransaction": _payme_check,
    "GetStatement": _payme_statement,
}


# ----- Uzum -----------------------------------------------------------------


def _uzum_invoice_id(body: dict) -> Optional[int]:
    """Uzum's payload names the invoice under one of a few keys depending on
    the integration flavour — accept them all, reject anything non-numeric."""
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    for source in (params, body):
        for key in ("id", "orderId", "order_id", "account", "invoiceId"):
            raw = source.get(key)
            if raw in (None, ""):
                continue
            try:
                return int(str(raw).strip())
            except (TypeError, ValueError):
                continue
    return None


# Uzum's published error codes (developer.uzumbank.uz/merchant). They are
# **strings**, and every failure is HTTP 400 with {serviceId, timestamp,
# status: "FAILED", errorCode} — an invented code or a 401/404 is rejected by
# their validator, so these are copied verbatim rather than improvised.
UZ_AUTH = "10001"
UZ_PARSE = "10002"
UZ_METHOD = "10003"
UZ_MISSING_PARAMS = "10005"
UZ_SERVICE_ID = "10006"
UZ_NOT_FOUND = "10007"
UZ_ALREADY_PAID = "10008"
UZ_CANCELLED = "10009"
UZ_TX_EXISTS = "10010"
UZ_AMOUNT = "10011"
UZ_TX_NOT_FOUND = "10014"
UZ_TX_CANCELLED = "10015"
UZ_TX_CONFIRMED = "10016"
UZ_INTERNAL = "99999"


def _uzum_ok(body: dict, payload: dict) -> dict:
    """Success envelope. `serviceId` is echoed from the request, not taken from
    our config: Uzum matches it against what it sent."""
    return {
        "serviceId": body.get("serviceId") or config.uzum_service_id,
        "timestamp": _now_ms(),
        **payload,
    }


def _uzum_fail(body: dict, code: str) -> JSONResponse:
    return JSONResponse(
        {
            "serviceId": (body or {}).get("serviceId") or config.uzum_service_id,
            "timestamp": _now_ms(),
            "status": "FAILED",
            "errorCode": code,
        },
        status_code=400,
    )


async def _uzum_lookup(body: dict):
    payment_id = _uzum_invoice_id(body)
    if payment_id is None:
        return None, _uzum_fail(body, UZ_MISSING_PARAMS)
    payment = await get_repository().get_payment(payment_id)
    if payment is None:
        return None, _uzum_fail(body, UZ_NOT_FOUND)
    return payment, None


def create_app():
    """Build the FastAPI app (uvicorn factory)."""
    app = FastAPI(title="Maskan payments", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "payme": payments.payme_enabled(),
            "uzum": payments.uzum_enabled(),
        }

    @app.post("/payme")
    async def payme_rpc(
        request: Request, authorization: Optional[str] = Header(default=None)
    ):
        body = await request.json()
        rpc_id = body.get("id") if isinstance(body, dict) else None
        if not payments.payme_auth_ok(authorization):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": rpc_id, **_err(ERR_AUTH, "Ruxsat yo'q")}
            )
        method = (body or {}).get("method")
        params = (body or {}).get("params") or {}
        handler = _PAYME_METHODS.get(method)
        if handler is None:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": rpc_id, **_err(ERR_METHOD, f"Noma'lum metod: {method}")}
            )
        try:
            result = await handler(params)
        except Exception:
            logger.exception("Payme %s failed", method)
            result = _err(ERR_TRANSPORT, "Ichki xatolik")
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, **result})

    @app.post("/uzum/{action}")
    async def uzum_callback(
        action: str,
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ):
        try:
            body = await request.json()
        except Exception:
            return _uzum_fail({}, UZ_PARSE)
        if not payments.uzum_auth_ok(authorization):
            return _uzum_fail(body, UZ_AUTH)
        if action not in ("check", "create", "confirm", "reverse"):
            return _uzum_fail(body, UZ_METHOD)

        payment, error = await _uzum_lookup(body)
        if error is not None:
            return error
        repo = get_repository()
        trans_id = str(body.get("transId") or "")

        if action == "check":
            if payment.state == PAYMENT_CANCELLED:
                return _uzum_fail(body, UZ_CANCELLED)
            if payment.state == PAYMENT_PAID:
                return _uzum_fail(body, UZ_ALREADY_PAID)
            # `data.amount.value` is the one figure Uzum shows the customer in
            # its app, and it is in SO'M as a string — the only place in this
            # module that is not tiyin. Get it wrong and the customer is asked
            # to confirm a payment 100× too large.
            return JSONResponse(_uzum_ok(body, {
                "status": "OK",
                "data": {"amount": {"value": str(payments.tiyin_to_som(payment.amount_tiyin))}},
            }))

        if action == "create":
            if payment.state == PAYMENT_PAID:
                return _uzum_fail(body, UZ_ALREADY_PAID)
            if payment.state == PAYMENT_CANCELLED:
                return _uzum_fail(body, UZ_CANCELLED)
            if not trans_id:
                return _uzum_fail(body, UZ_MISSING_PARAMS)
            # A second create for a transaction we already opened is a duplicate,
            # not a retry of a different payment.
            if payment.provider_txn_id and payment.provider_txn_id != trans_id:
                return _uzum_fail(body, UZ_TX_EXISTS)
            # The amount Uzum is about to charge must equal the invoice. Without
            # this check a request naming a smaller sum would open a transaction
            # that later confirms and marks the order paid in full.
            amount = int(body.get("amount") or 0)
            if amount != int(payment.amount_tiyin):
                return _uzum_fail(body, UZ_AMOUNT)
            await repo.update_payment(
                payment.id,
                provider=PROVIDER_UZUM,
                provider_txn_id=trans_id,
                state=PAYMENT_CREATED,
                create_time=payment.create_time or _now_ms(),
            )
            return JSONResponse(_uzum_ok(body, {
                "status": "CREATED",
                "transId": trans_id,
                "amount": int(payment.amount_tiyin),
            }))

        if action == "confirm":
            if not trans_id:
                return _uzum_fail(body, UZ_MISSING_PARAMS)
            if not payment.provider_txn_id:
                return _uzum_fail(body, UZ_TX_NOT_FOUND)
            if payment.provider_txn_id != trans_id:
                return _uzum_fail(body, UZ_TX_NOT_FOUND)
            if payment.state == PAYMENT_CANCELLED:
                return _uzum_fail(body, UZ_TX_CANCELLED)
            if payment.state == PAYMENT_PAID:
                # Idempotent: Uzum retries confirm, and a retry must not read as
                # an error — but it must not re-run the "thank the customer"
                # side effects either. The watcher already stamped notified_at.
                return JSONResponse(_uzum_ok(body, {
                    "status": "CONFIRMED",
                    "transId": trans_id,
                    "amount": int(payment.amount_tiyin),
                }))
            await repo.update_payment(
                payment.id,
                provider=PROVIDER_UZUM,
                state=PAYMENT_PAID,
                perform_time=_now_ms(),
            )
            logger.info("Uzum payment %s confirmed (transId=%s)", payment.id, trans_id)
            return JSONResponse(_uzum_ok(body, {
                "status": "CONFIRMED",
                "transId": trans_id,
                "amount": int(payment.amount_tiyin),
            }))

        # reverse
        if not trans_id or payment.provider_txn_id != trans_id:
            return _uzum_fail(body, UZ_TX_NOT_FOUND)
        if payment.state == PAYMENT_CANCELLED:
            return JSONResponse(_uzum_ok(body, {"status": "REVERSED", "transId": trans_id}))
        await repo.update_payment(
            payment.id, state=PAYMENT_CANCELLED, cancel_time=_now_ms()
        )
        logger.info("Uzum payment %s reversed (transId=%s)", payment.id, trans_id)
        return JSONResponse(_uzum_ok(body, {"status": "REVERSED", "transId": trans_id}))

    return app


def run() -> None:
    """Console entrypoint (`maskan-payments-api`)."""
    import sys

    from db.engine import database_configured

    if not database_configured():
        sys.stderr.write("maskan-payments-api: DATABASE_URL is not set.\n")
        raise SystemExit(2)
    if not payments.any_provider_enabled():
        sys.stderr.write(
            "maskan-payments-api: no payment provider configured "
            "(set MASKAN_PAYME_MERCHANT_ID and/or MASKAN_UZUM_SERVICE_ID).\n"
        )
        raise SystemExit(2)

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    uvicorn.run(
        "apps.maskan.payments_api:create_app",
        factory=True,
        host=config.payments_api_host,
        port=config.payments_api_port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
