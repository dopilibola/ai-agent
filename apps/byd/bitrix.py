"""Bitrix24 CRM mirror — REST client + push operations for the BYD funnel.

Talks to the portal through an **incoming webhook** (`BYD_BITRIX_WEBHOOK_URL`,
scopes `crm` + `task`). Everything here is called from the scheduled-task
executors in `funnel.py` (durable queue → retry/backoff on failure) and from the
pull job in `bitrix_sync.py`; nothing imports the Telegram runtime, and this
module never imports `funnel` (no cycle).

Conventions (per apidocs.bitrix24.com):
* classic CRM methods (`crm.deal.*`, `crm.contact.*`, `crm.status.*`,
  `crm.timeline.*`, userfields) take UPPER_CASE keys under `fields`;
* `tasks.task.*` takes UPPER_CASE fields but answers in camelCase;
* `date` fields are "YYYY-MM-DD", datetimes ISO 8601 with the tz offset;
* rate limit is ~2 req/s sustained (503 `QUERY_LIMIT_EXCEEDED`, 429
  `OPERATION_TIME_LIMIT`) — `BitrixClient.call` backs off and retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from apps.byd.config import BydConfig, config
from apps.byd.models import STAGE_DONE, STATUS_CLOSED, BydLead
from apps.byd.repository import get_repository

logger = logging.getLogger(__name__)


# ===== deal custom fields (created by scripts/byd_bitrix_setup.py) ==========
# FIELD_NAME (≤13 chars) → the portal stores it as UF_CRM_<FIELD_NAME>.

UF_CHAT_ID = "UF_CRM_TG_CHAT_ID"
UF_TG_USERNAME = "UF_CRM_TG_USERNAME"
UF_CITY = "UF_CRM_CITY"
UF_PROGRAM = "UF_CRM_PROGRAM"
UF_ARRIVAL = "UF_CRM_ARRIVAL"
UF_DOB = "UF_CRM_DOB"
UF_VOUCHER = "UF_CRM_VOUCHER"

# (FIELD_NAME, USER_TYPE_ID, label) — consumed by the setup script.
DEAL_USERFIELDS: tuple[tuple[str, str, str], ...] = (
    ("TG_CHAT_ID", "string", "Telegram chat id"),
    ("TG_USERNAME", "string", "Telegram username"),
    ("CITY", "string", "Город"),
    ("PROGRAM", "string", "Программа (7/14/21)"),
    ("ARRIVAL", "date", "Дата заезда"),
    ("DOB", "date", "Дата рождения"),
    ("VOUCHER", "string", "Номер ваучера"),
)

# ===== stage codes ===========================================================
# STATUS_ID codes within the funnel category. `NEW`/`WON`/`LOSE` come with any
# new category; BYD_S2..BYD_S7 are created by the setup script. The full deal
# STAGE_ID is `C{category}:{code}` (unprefixed for the default category 0).

STAGE_CODES: dict[int, str] = {
    1: "NEW",
    2: "BYD_S2",
    3: "BYD_S3",
    4: "BYD_S4",
    5: "BYD_S5",
    6: "BYD_S6",
    7: "BYD_S7",
    8: "WON",
}
LOSE_CODE = "LOSE"


def _prefix(cfg: BydConfig) -> str:
    cat = cfg.bitrix_category_id or 0
    return f"C{cat}:" if cat else ""


def stage_map(cfg: BydConfig = config) -> dict[int, str]:
    """Local funnel stage (1..8) → Bitrix STAGE_ID. `BYD_BITRIX_STAGE_MAP`
    (JSON, keys "1".."8" + optional "lose") overrides for hand-built funnels."""
    if cfg.bitrix_stage_map_json:
        raw = json.loads(cfg.bitrix_stage_map_json)
        return {int(k): str(v) for k, v in raw.items() if str(k).isdigit()}
    p = _prefix(cfg)
    return {n: p + code for n, code in STAGE_CODES.items()}


def lose_stage_id(cfg: BydConfig = config) -> str:
    if cfg.bitrix_stage_map_json:
        raw = json.loads(cfg.bitrix_stage_map_json)
        if raw.get("lose"):
            return str(raw["lose"])
    return _prefix(cfg) + LOSE_CODE


def local_stage_for(stage_id: str, cfg: BydConfig = config) -> Optional[int]:
    """Bitrix STAGE_ID → local stage number, None if unmapped (e.g. a custom
    failure stage — the caller falls back to STAGE_SEMANTIC_ID)."""
    for n, sid in stage_map(cfg).items():
        if sid == stage_id:
            return n
    return None


def desired_stage_id(lead: BydLead, cfg: BydConfig = config) -> str:
    """The STAGE_ID this lead should occupy: its funnel stage, or the failure
    stage once it's closed (a lead closed after «Успешно реализовано» stays won).
    A deal an operator already parked on a specific failure stage in Bitrix
    (unmapped STAGE_ID) keeps that stage — we never overwrite the chosen loss
    reason with the generic LOSE."""
    if lead.status == STATUS_CLOSED and lead.current_stage != STAGE_DONE:
        current = lead.bitrix_stage_id or ""
        if current and local_stage_for(current, cfg) is None:
            return current
        return lose_stage_id(cfg)
    return stage_map(cfg).get(lead.current_stage, _prefix(cfg) + "NEW")


def portal_base(cfg: BydConfig = config) -> str:
    host = urlparse(cfg.bitrix_webhook_url).netloc
    return f"https://{host}" if host else ""


def deal_url(deal_id: int, cfg: BydConfig = config) -> str:
    base = portal_base(cfg)
    return f"{base}/crm/deal/details/{int(deal_id)}/" if base else ""


# ===== REST client ==========================================================

class BitrixError(RuntimeError):
    """The portal answered with an error envelope."""

    def __init__(self, code: str, description: str = ""):
        super().__init__(f"{code}: {description}" if description else code)
        self.code = code
        self.description = description


_RETRYABLE = ("QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT", "OVERLOAD_LIMIT")


class BitrixClient:
    """Thin async webhook client: POST JSON, unwrap `result`, back off on the
    portal's rate limits. Raises `BitrixError` on API errors, httpx errors on
    transport failures — both bubble to the scheduler for durable retry."""

    def __init__(self, webhook_url: str, timeout: int = 30):
        self._base = webhook_url.rstrip("/") + "/"
        self._http = httpx.AsyncClient(timeout=timeout)

    async def call(self, method: str, payload: Optional[dict] = None) -> Any:
        url = self._base + method + ".json"
        last = "rate limit"
        for attempt in range(5):
            if attempt:
                await asyncio.sleep(1.5 * attempt)
            resp = await self._http.post(url, json=payload or {})
            try:
                data = resp.json()
            except ValueError:
                resp.raise_for_status()
                raise BitrixError("BAD_RESPONSE", resp.text[:200])
            if isinstance(data, dict) and data.get("error"):
                code = str(data["error"])
                if code in _RETRYABLE:
                    last = code
                    continue
                raise BitrixError(code, str(data.get("error_description") or ""))
            if resp.status_code >= 400:
                resp.raise_for_status()
            return data.get("result") if isinstance(data, dict) else data
        raise BitrixError(last, "retries exhausted")

_client: Optional[BitrixClient] = None


def get_client(cfg: BydConfig = config) -> BitrixClient:
    global _client
    if _client is None:
        _client = BitrixClient(cfg.bitrix_webhook_url, timeout=cfg.request_timeout)
    return _client


# ===== helpers ==============================================================

def _fmt_date(d: Optional[date]) -> str:
    return d.isoformat() if d else ""


def _origin_id(lead: BydLead) -> str:
    return f"byd_lead_{lead.id}"


def _contact_fields(lead: BydLead, cfg: BydConfig) -> dict:
    fields: dict = {
        "NAME": lead.name or f"Telegram {lead.chat_id}",
        "TYPE_ID": "CLIENT",
        "SOURCE_ID": "OTHER",
    }
    if cfg.bitrix_assigned_by_id:
        fields["ASSIGNED_BY_ID"] = cfg.bitrix_assigned_by_id
    if lead.phone:
        fields["PHONE"] = [{"VALUE": lead.phone, "VALUE_TYPE": "MOBILE"}]
    if lead.tg_username:
        fields["IM"] = [
            {"VALUE": f"https://t.me/{lead.tg_username}", "VALUE_TYPE": "TELEGRAM"}
        ]
    return fields


def _deal_fields(lead: BydLead, cfg: BydConfig) -> dict:
    title = lead.name or "Клиент Telegram"
    if lead.city:
        title += f" · {lead.city}"
    fields: dict = {
        "TITLE": title,
        "COMMENTS": lead.request or "",
        "CURRENCY_ID": cfg.bitrix_currency,
        UF_CHAT_ID: str(lead.chat_id),
        UF_TG_USERNAME: lead.tg_username or "",
        UF_CITY: lead.city or "",
        UF_PROGRAM: lead.program_code or "",
        UF_ARRIVAL: _fmt_date(lead.arrival_date),
        UF_DOB: _fmt_date(lead.date_of_birth),
        UF_VOUCHER: str(lead.voucher_number or ""),
    }
    if lead.total_amount:
        fields["OPPORTUNITY"] = int(lead.total_amount)
        fields["IS_MANUAL_OPPORTUNITY"] = "Y"
    if cfg.bitrix_assigned_by_id:
        fields["ASSIGNED_BY_ID"] = cfg.bitrix_assigned_by_id
    if lead.arrival_date:
        fields["CLOSEDATE"] = _fmt_date(lead.arrival_date)
    return fields


async def _find_deal_by_origin(client: BitrixClient, lead: BydLead) -> Optional[int]:
    """Duplicate-create guard: a previous create may have succeeded without our
    ref write landing (crash between the two). ORIGIN_ID makes it findable."""
    rows = await client.call(
        "crm.deal.list",
        {"filter": {"=ORIGIN_ID": _origin_id(lead)}, "select": ["ID"], "start": -1},
    )
    if rows:
        return int(rows[0]["ID"])
    return None


# ===== push operations (called by the funnel's bitrix_* executors) ==========

async def sync_lead(lead: BydLead, cfg: BydConfig = config) -> None:
    """Full-state push: ensure the contact + deal exist and reflect the lead.

    The stage is written only when it differs from `bitrix_stage_id` (the last
    state we synced) — so a concurrent operator move in Bitrix is never clobbered
    by a field-only push, and a pull-applied stage isn't pushed back (no echo)."""
    if not cfg.bitrix_enabled:
        return
    repo = get_repository()
    client = get_client(cfg)

    contact_id = lead.bitrix_contact_id
    if contact_id is None:
        contact_id = int(
            await client.call("crm.contact.add", {"fields": _contact_fields(lead, cfg)})
        )
        await repo.set_bitrix_refs(lead.id, contact_id=contact_id)
    else:
        # Keep the display name fresh; multifields are append-on-update in
        # Bitrix, so PHONE/IM are written once at creation only.
        await client.call(
            "crm.contact.update",
            {
                "id": contact_id,
                "fields": {"NAME": lead.name or f"Telegram {lead.chat_id}"},
            },
        )

    desired = desired_stage_id(lead, cfg)
    fields = _deal_fields(lead, cfg)
    fields["CONTACT_ID"] = contact_id

    deal_id = lead.bitrix_deal_id
    if deal_id is None:
        deal_id = await _find_deal_by_origin(client, lead)
        if deal_id is None:
            fields["CATEGORY_ID"] = cfg.bitrix_category_id
            fields["STAGE_ID"] = desired
            fields["OPENED"] = "Y"
            fields["ORIGIN_ID"] = _origin_id(lead)
            fields["SOURCE_DESCRIPTION"] = "Telegram — ИИ-бот Нигина"
            deal_id = int(
                await client.call(
                    "crm.deal.add",
                    {"fields": fields, "params": {"REGISTER_SONET_EVENT": "Y"}},
                )
            )
            await repo.set_bitrix_refs(lead.id, deal_id=deal_id, stage_id=desired)
            logger.info("Bitrix deal %s created for BYD lead %s", deal_id, lead.id)
            return
        await repo.set_bitrix_refs(lead.id, deal_id=deal_id)

    update = dict(fields)
    push_stage = desired != (lead.bitrix_stage_id or "")
    if push_stage:
        update["STAGE_ID"] = desired
    await client.call("crm.deal.update", {"id": int(deal_id), "fields": update})
    if push_stage:
        await repo.set_bitrix_refs(lead.id, stage_id=desired)


async def _ensure_deal(lead: BydLead, cfg: BydConfig) -> Optional[BydLead]:
    """Return a lead that has a Bitrix deal, creating it if needed."""
    if lead.bitrix_deal_id is not None:
        return lead
    await sync_lead(lead, cfg)
    return await get_repository().get_lead(lead.id)


async def post_lead_comment(lead: BydLead, text: str, cfg: BydConfig = config) -> None:
    """Timeline comment on the lead's deal (creates the deal first if the
    creation push hasn't landed yet — raising lets the queue retry)."""
    if not cfg.bitrix_enabled or not text:
        return
    fresh = await _ensure_deal(lead, cfg)
    if fresh is None or fresh.bitrix_deal_id is None:
        raise RuntimeError(f"BYD lead {lead.id} has no Bitrix deal yet")
    await get_client(cfg).call(
        "crm.timeline.comment.add",
        {
            "fields": {
                "ENTITY_ID": int(fresh.bitrix_deal_id),
                "ENTITY_TYPE": "deal",
                "COMMENT": text,
            }
        },
    )


async def push_operator_task(
    op_task_id: int, lead: BydLead, cfg: BydConfig = config
) -> None:
    """Mirror a local operator task as a Bitrix task bound to the deal."""
    if not cfg.bitrix_enabled:
        return
    repo = get_repository()
    task = await repo.get_operator_task(op_task_id)
    if task is None or task.bitrix_task_id is not None:
        return
    responsible = cfg.bitrix_task_responsible_id or cfg.bitrix_assigned_by_id
    if not responsible:
        logger.warning(
            "BYD_BITRIX_TASK_RESPONSIBLE_ID/ASSIGNED_BY_ID not set — "
            "skipping Bitrix mirror of operator task %s", op_task_id
        )
        return
    fresh = await _ensure_deal(lead, cfg)
    if fresh is None or fresh.bitrix_deal_id is None:
        raise RuntimeError(f"BYD lead {lead.id} has no Bitrix deal yet")

    description = f"Клиент: {lead.name or '—'}"
    if lead.tg_username:
        description += f"\nTelegram: https://t.me/{lead.tg_username}"
    description += f"\nСделка: {deal_url(fresh.bitrix_deal_id, cfg)}"

    fields: dict = {
        "TITLE": task.title[:250] or "Задача по клиенту BYD",
        "DESCRIPTION": description,
        "RESPONSIBLE_ID": responsible,
        "UF_CRM_TASK": [f"D_{int(fresh.bitrix_deal_id)}"],
    }
    if task.due_at is not None:
        fields["DEADLINE"] = task.due_at.isoformat()
    result = await get_client(cfg).call("tasks.task.add", {"fields": fields})
    bitrix_task_id = int(result["task"]["id"])  # response is camelCase
    await repo.set_operator_task_bitrix_id(task.id, bitrix_task_id)


async def complete_operator_task(op_task_id: int, cfg: BydConfig = config) -> None:
    """Close the Bitrix mirror of a completed local task. An API refusal (e.g.
    already completed by the operator in Bitrix) is fine; transport errors
    bubble so the queue retries."""
    if not cfg.bitrix_enabled:
        return
    task = await get_repository().get_operator_task(op_task_id)
    if task is None or task.bitrix_task_id is None:
        return
    try:
        await get_client(cfg).call(
            "tasks.task.complete", {"taskId": int(task.bitrix_task_id)}
        )
    except BitrixError as exc:
        logger.info(
            "Bitrix task %s complete refused (%s) — treating as done",
            task.bitrix_task_id, exc.code,
        )


# ===== used by the pull job =================================================

async def fetch_tracked_deals(
    deal_ids: list[int], cfg: BydConfig = config
) -> dict[int, dict]:
    """Live state of the given deals, keyed by id. Chunks of 50 = exactly one
    page per request, no pagination bookkeeping."""
    client = get_client(cfg)
    out: dict[int, dict] = {}
    select = [
        "ID",
        "STAGE_ID",
        "STAGE_SEMANTIC_ID",
        "OPPORTUNITY",
        "DATE_MODIFY",
        UF_PROGRAM,
        UF_ARRIVAL,
        UF_DOB,
    ]
    for i in range(0, len(deal_ids), 50):
        chunk = [int(d) for d in deal_ids[i : i + 50]]
        rows = await client.call(
            "crm.deal.list",
            {"filter": {"@ID": chunk}, "select": select, "start": -1},
        )
        for row in rows or []:
            out[int(row["ID"])] = row
    return out


async def stage_names(cfg: BydConfig = config) -> dict[str, str]:
    """STAGE_ID → human name for the funnel's category (used to word close
    reasons pulled from Bitrix failure stages)."""
    cat = cfg.bitrix_category_id or 0
    entity = f"DEAL_STAGE_{cat}" if cat else "DEAL_STAGE"
    rows = await get_client(cfg).call(
        "crm.status.list", {"filter": {"ENTITY_ID": entity}}
    )
    return {str(r["STATUS_ID"]): str(r.get("NAME") or r["STATUS_ID"]) for r in rows or []}
