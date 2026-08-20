"""One-time Bitrix24 portal provisioning for the BYD funnel.

Creates (idempotently) everything the integration expects in the client's
portal, then prints the .env lines to set:

* the deal pipeline («BYD Medical — Воронка продаж», entityTypeId 2);
* the 8 funnel stages from the ТЗ (renames the auto-created NEW/WON/LOSE,
  adds BYD_S2..BYD_S7, removes the unused default intermediate stages);
* the failure stages — one per «причина закрытия» from the ТЗ;
* the deal custom fields (UF_CRM_TG_CHAT_ID, …) the sync reads/writes.

Usage:
    uv run python scripts/byd_bitrix_setup.py            # BYD_BITRIX_WEBHOOK_URL from .env
    uv run python scripts/byd_bitrix_setup.py --webhook https://xxx.bitrix24.ru/rest/1/token/
    uv run python scripts/byd_bitrix_setup.py --backfill # + queue a sync of every
                                                         # existing non-closed lead
                                                         # (run AFTER the env vars
                                                         # are set; needs DATABASE_URL)

The webhook needs the `crm` + `task` scopes and a user with CRM-admin rights
(pipeline/stage creation is admin-only).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.byd.bitrix import (  # noqa: E402
    DEAL_USERFIELDS,
    LOSE_CODE,
    STAGE_CODES,
    BitrixClient,
    BitrixError,
)
from apps.byd.models import STAGE_TITLES_RU  # noqa: E402

CATEGORY_NAME = "BYD Medical — Воронка продаж"

# In-progress + won stage colors (kanban), keyed by local stage number.
STAGE_COLORS = {
    1: "#39A8EF", 2: "#2FC6F6", 3: "#55D0E0", 4: "#47E4C2",
    5: "#FFA900", 6: "#7BD500", 7: "#4CAF50", 8: "#00C4FB",
}

# Failure stages — the ТЗ's «причины закрытия» per stage, as SEMANTICS=F
# statuses so the operator picks the loss reason right on the kanban.
LOSS_STAGES: tuple[tuple[str, str], ...] = (
    ("BYD_L01", "Некорректные контактные данные"),
    ("BYD_L02", "Ложная или случайная заявка"),
    ("BYD_L03", "Потеря заявки (обработка не вовремя)"),
    ("BYD_L04", "Не выходит на связь (5+ касаний)"),
    ("BYD_L05", "Отказ от общения"),
    ("BYD_L06", "Нет интереса к услугам"),
    ("BYD_L07", "Возражения не отработаны"),
    ("BYD_L08", "Отказ после консультации"),
    ("BYD_L09", "Противопоказания по здоровью"),
    ("BYD_L10", "Не готов к оплате"),
    ("BYD_L11", "Сомнения в эффективности"),
    ("BYD_L12", "Нет денег / просит рассрочку"),
    ("BYD_L13", "Отмена брони после оплаты"),
    ("BYD_L14", "Не подтвердил дату заезда"),
    ("BYD_L15", "Не отвечает на звонок (заезд)"),
    ("BYD_L16", "Отмена визита в последний момент"),
    ("BYD_L17", "Не беспокоить (просьба клиента)"),
)


async def ensure_category(client: BitrixClient) -> int:
    result = await client.call("crm.category.list", {"entityTypeId": 2})
    for cat in (result or {}).get("categories", []):
        if cat.get("name") == CATEGORY_NAME:
            print(f"✓ Pipeline exists: «{CATEGORY_NAME}» (id {cat['id']})")
            return int(cat["id"])
    result = await client.call(
        "crm.category.add",
        {"entityTypeId": 2, "fields": {"name": CATEGORY_NAME, "sort": 200}},
    )
    cat_id = int(result["category"]["id"])
    print(f"✓ Pipeline created: «{CATEGORY_NAME}» (id {cat_id})")
    return cat_id


async def ensure_stages(client: BitrixClient, cat_id: int) -> dict[int, str]:
    entity = f"DEAL_STAGE_{cat_id}"
    prefix = f"C{cat_id}:"

    async def list_stages() -> dict[str, dict]:
        rows = await client.call("crm.status.list", {"filter": {"ENTITY_ID": entity}})
        return {str(r["STATUS_ID"]): r for r in rows or []}

    existing = await list_stages()
    want: dict[str, tuple[str, int, str, str]] = {}  # STATUS_ID → (name, sort, color, semantics)
    for n, code in STAGE_CODES.items():
        want[prefix + code] = (
            STAGE_TITLES_RU[n], n * 10, STAGE_COLORS[n], "S" if code == "WON" else ""
        )
    want[prefix + LOSE_CODE] = ("Закрыто (прочее)", 300, "#777777", "F")
    for i, (code, name) in enumerate(LOSS_STAGES):
        want[prefix + code] = (name, 310 + i * 10, "#999999", "F")

    for status_id, (name, sort, color, semantics) in want.items():
        row = existing.get(status_id)
        if row is None:
            fields = {
                "ENTITY_ID": entity,
                # The portal auto-prepends the C{cat}: prefix for deal stages.
                "STATUS_ID": status_id.removeprefix(prefix),
                "NAME": name,
                "SORT": sort,
                "COLOR": color,
            }
            if semantics:
                fields["SEMANTICS"] = semantics
            await client.call("crm.status.add", {"fields": fields})
            print(f"  + stage {status_id}  «{name}»")
        elif row.get("NAME") != name:
            await client.call(
                "crm.status.update", {"id": int(row["ID"]), "fields": {"NAME": name, "SORT": sort}}
            )
            print(f"  ~ stage {status_id} renamed → «{name}»")
        else:
            print(f"  ✓ stage {status_id}  «{name}»")

    # Drop the auto-created default intermediates we don't use (best-effort —
    # fails harmlessly if a deal already sits on one).
    existing = await list_stages()
    for status_id, row in existing.items():
        semantics = ((row.get("EXTRA") or {}).get("SEMANTICS") or "").upper()
        if status_id not in want and semantics not in ("S", "F"):
            try:
                await client.call("crm.status.delete", {"id": int(row["ID"])})
                print(f"  - removed unused default stage {status_id}")
            except BitrixError as exc:
                print(f"  ! could not remove {status_id}: {exc.code}")

    final = await list_stages()
    missing = [sid for sid in want if sid not in final]
    if missing:
        raise SystemExit(f"Stages did not materialize as expected: {missing}")
    return {n: prefix + code for n, code in STAGE_CODES.items()}


async def ensure_userfields(client: BitrixClient) -> None:
    rows = await client.call("crm.deal.userfield.list", {"filter": {"LANG": "ru"}})
    have = {str(r.get("FIELD_NAME") or "") for r in rows or []}
    for field_name, type_id, label in DEAL_USERFIELDS:
        full = f"UF_CRM_{field_name}"
        if full in have:
            print(f"  ✓ field {full}")
            continue
        await client.call(
            "crm.deal.userfield.add",
            {
                "fields": {
                    "FIELD_NAME": field_name,
                    "USER_TYPE_ID": type_id,
                    "EDIT_FORM_LABEL": label,
                    "LIST_COLUMN_LABEL": label,
                    "LIST_FILTER_LABEL": label,
                    "SHOW_FILTER": "Y",
                }
            },
        )
        print(f"  + field {full} ({type_id})  «{label}»")


async def backfill_leads() -> None:
    """Queue a `bitrix_sync` for every non-closed lead that predates the
    integration, so the running bot pushes them into the pipeline."""
    from datetime import datetime

    from apps.byd.config import CLINIC_TZ, config
    from apps.byd.models import STATUS_CLOSED, BydLead
    from apps.byd.repository import get_repository
    from sqlalchemy import select
    from db.engine import get_sessionmaker

    if not config.bitrix_enabled:
        raise SystemExit(
            "Set BYD_BITRIX_WEBHOOK_URL + BYD_BITRIX_CATEGORY_ID in .env before --backfill."
        )
    repo = get_repository()
    async with get_sessionmaker()() as session:
        leads = list(
            await session.scalars(select(BydLead).where(BydLead.status != STATUS_CLOSED))
        )
    now = datetime.now(CLINIC_TZ)
    rows = [
        {
            "lead_id": lead.id,
            "chat_id": lead.chat_id,
            "action_type": "bitrix_sync",
            "stage": 0,
            "scheduled_for": now,
            "dedup_key": f"bitrix:sync:{lead.id}:backfill",
            "payload": {},
        }
        for lead in leads
    ]
    queued = await repo.enqueue_tasks(rows)
    print(f"✓ Queued Bitrix sync for {queued} existing lead(s) — "
          "the running bot's scheduler will push them within a minute.")


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--webhook",
        default=os.environ.get("BYD_BITRIX_WEBHOOK_URL", ""),
        help="incoming webhook base URL (default: BYD_BITRIX_WEBHOOK_URL)",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="queue existing non-closed leads for a push into the pipeline",
    )
    args = parser.parse_args()
    if not args.webhook:
        raise SystemExit("Pass --webhook or set BYD_BITRIX_WEBHOOK_URL in .env")

    client = BitrixClient(args.webhook)

    profile = await client.call("profile")
    user_id = int(profile.get("ID", 0))
    print(
        f"Connected to the portal as {profile.get('NAME', '')} "
        f"{profile.get('LAST_NAME', '')} (user id {user_id})\n"
    )

    cat_id = await ensure_category(client)
    print("Stages:")
    stage_map = await ensure_stages(client, cat_id)
    print("Deal fields:")
    await ensure_userfields(client)

    print("\nDone. Add to .env:\n")
    print(f"BYD_BITRIX_WEBHOOK_URL={args.webhook}")
    print(f"BYD_BITRIX_CATEGORY_ID={cat_id}")
    print(f"BYD_BITRIX_ASSIGNED_BY_ID={user_id}  # or the operator's user id")
    print("# BYD_BITRIX_TASK_RESPONSIBLE_ID=<operator user id>  # tasks assignee")
    print(
        "# Stage ids (derived automatically — set BYD_BITRIX_STAGE_MAP only if you "
        "renamed/rebuilt stages by hand):\n# BYD_BITRIX_STAGE_MAP="
        + json.dumps(stage_map, ensure_ascii=False)
    )

    if args.backfill:
        print()
        await backfill_leads()


if __name__ == "__main__":
    asyncio.run(main())
