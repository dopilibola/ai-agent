"""Run a tenant's eval cases against the live agent.

    uv run python scripts/eval_run.py --tenant maskan
    uv run python scripts/eval_run.py --tenant maskan --case qabriston-imlo-xato
    uv run python scripts/eval_run.py --tenant maskan --json report.json

Each case runs in its own throwaway chat id inside a reserved range, under the
tenant id `<tenant>-eval` so its rows are separable from real traffic in the
corpus (and excluded from `corpus_report.py`, which filters on tenant). Any
leads, orders and graves the run creates are deleted afterwards — an eval must
not leave a fake customer in the funnel, or the scheduler will start writing to
a chat id that does not exist.

Exit code is 1 if any case fails, so this can gate a deploy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.learning.evals import Case, CaseResult, check, collect_trace, render  # noqa: E402

# Reserved so an eval chat id can never collide with a Telegram user id
# (Telegram ids are far larger, and the funnel's own test rows sit below this).
EVAL_CHAT_BASE = 990_000_000


class StubChannel:
    """The Telegram channel as far as the tools and funnel are concerned —
    answers identity questions, swallows every send."""

    name = "customer"

    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id
        self.sent: list[str] = []

    async def get_chat_info(self, chat_id):
        return {"username": "eval", "name": "Eval Mijoz"}

    async def send_text(self, chat_id, text):
        self.sent.append(text)

    async def record_outbound(self, chat_id, note):
        pass

    async def send_photos(self, chat_id, urls):
        pass

    async def compose_outbound(self, chat_id, convey):
        return "[eval]"


def connect():
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL o'rnatilmagan.")
    return psycopg.connect(dsn.replace("postgresql+psycopg", "postgresql"))


async def allowed_amounts() -> set[int]:
    """Every figure the agent is allowed to say out loud: each service price,
    plus the sums it can legitimately quote for a multi-service order."""
    from itertools import combinations

    from apps.maskan.repository import get_repository

    prices = [s.price for s in await get_repository().list_services()]
    allowed = set(prices)
    for size in range(2, min(len(prices), 4) + 1):
        for combo in combinations(prices, size):
            allowed.add(sum(combo))
    return allowed


async def cleanup(chat_id: int) -> None:
    """Remove everything the case created. Best-effort per table so one missing
    table cannot leave the rest behind."""
    from apps.maskan.repository import get_repository

    repo = get_repository()
    try:
        await repo.cancel_pending_for_chat(chat_id)
        lead = await repo.get_active_lead_by_chat(chat_id)
        if lead is not None:
            await repo.set_status(lead.id, "closed")
    except Exception:
        pass
    with connect() as conn:
        cur = conn.cursor()
        for table in ("maskan_scheduled_tasks", "maskan_payments", "maskan_orders",
                      "maskan_graves", "maskan_leads"):
            try:
                cur.execute(f"delete from {table} where chat_id = %s", (chat_id,))
            except Exception:
                conn.rollback()
        conn.commit()


async def run_case(case: Case, index: int, *, amounts: set[int], keep: bool) -> CaseResult:
    from apps.maskan import funnel
    from apps.maskan.config import config
    from apps.maskan.main import _build_sales_agent
    from core import context as ctx
    from db.checkpointer import checkpointer_scope

    chat_id = EVAL_CHAT_BASE + index
    thread_id = f"maskan-eval:customer:{chat_id}"
    stub = StubChannel(chat_id)

    async with checkpointer_scope() as cp:
        agent = _build_sales_agent(cp)
        funnel.set_context(funnel.FunnelContext(
            config=config, customer_channel=stub, notifier=None, mute_store=None,
        ))
        ctx.current_tenant_id.set("maskan-eval")
        ctx.current_chat_id.set(chat_id)
        ctx.current_channel.set(stub)
        for turn in case.turns:
            try:
                await agent.invoke(turn, thread_id=thread_id)
            except Exception as exc:
                return CaseResult(case.id, False, [f"invoke yiqildi: {type(exc).__name__}: {exc}"])

    with connect() as conn:
        replies, tools, failed = await collect_trace(conn, thread_id)
        if not keep:
            cur = conn.cursor()
            cur.execute("delete from conversation_events where thread_id = %s", (thread_id,))
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                try:
                    cur.execute(f"delete from {table} where thread_id = %s", (thread_id,))
                except Exception:
                    conn.rollback()
            conn.commit()
    await cleanup(chat_id)

    return check(case, replies=replies, tools=tools, failed_tools=failed,
                 allowed_amounts=amounts)


async def main_async(args) -> int:
    cases_path = ROOT / "apps" / args.tenant / "evals" / "cases.jsonl"
    if not cases_path.exists():
        raise SystemExit(f"{cases_path} topilmadi.")
    cases = Case.load(cases_path)
    if args.case:
        cases = [c for c in cases if c.id in args.case]
        if not cases:
            raise SystemExit("Bunday id li keys yo'q.")

    from db import training

    if not training.enabled():
        raise SystemExit("TRAINING_LOG=0 — eval tool izini korpusdan o'qiydi, uni yoqing.")

    amounts = await allowed_amounts()
    print(f"{len(cases)} keys, katalogda {len(amounts)} ta ruxsat etilgan summa\n")

    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        print(f"  … {case.id}", flush=True)
        results.append(await run_case(case, index, amounts=amounts, keep=args.keep))

    print("\n" + render(results))
    if args.json:
        Path(args.json).write_text(json.dumps([
            {"id": r.case_id, "passed": r.passed, "failures": r.failures,
             "tools": r.tools, "replies": r.replies}
            for r in results
        ], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n→ {args.json}")
    return 0 if all(r.passed for r in results) else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", default="maskan")
    ap.add_argument("--case", nargs="*", help="faqat shu id(lar)ni ishlat")
    ap.add_argument("--json", help="natijani JSON faylga yoz")
    ap.add_argument("--keep", action="store_true", help="korpus satrlarini o'chirma (debug)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR)
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
