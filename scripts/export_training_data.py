"""Inspect and export the conversation corpus (`conversation_events`).

Every customer turn the agents handle is appended to `conversation_events` by
`db/training.py` — the customer's message, each tool call with its arguments and
whether it succeeded, the reply, the scheduled outbound touches, and the funnel
outcome (stage/status changes). This script is the read side:

    # what has been collected so far
    uv run python scripts/export_training_data.py --stats
    uv run python scripts/export_training_data.py --stats --tenant maskan --since 2026-08-01

    # one JSON object per conversation, ready for fine-tuning / few-shot mining
    uv run python scripts/export_training_data.py --out data/training/maskan.jsonl \
        --tenant maskan --min-turns 2 --outcome-only

Requires DATABASE_URL. Read-only — it never writes to the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"--since: not an ISO date/datetime: {value!r}")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _load(tenant: str | None, since: datetime | None) -> list:
    from sqlalchemy import select

    from db.engine import database_configured, get_sessionmaker
    from db.models import ConversationEvent

    if not database_configured():
        raise SystemExit("DATABASE_URL is not set — nothing to read.")

    stmt = select(ConversationEvent).order_by(ConversationEvent.id)
    if tenant:
        stmt = stmt.where(ConversationEvent.tenant_id == tenant)
    if since:
        stmt = stmt.where(ConversationEvent.created_at >= since)
    async with get_sessionmaker()() as session:
        return list(await session.scalars(stmt))


def _group(events: list) -> dict[str, list]:
    threads: dict[str, list] = defaultdict(list)
    for ev in events:
        threads[ev.thread_id or f"chat:{ev.chat_id}"].append(ev)
    return threads


def _dialogue(thread_id: str, events: list) -> dict:
    """One conversation as a training record: messages in order + outcome."""
    messages: list[dict] = []
    tools: dict[str, dict] = defaultdict(lambda: {"calls": 0, "errors": 0})
    langs: Counter = Counter()
    outcome: dict = {"events": []}
    tokens = 0
    turns = 0
    for ev in events:
        meta = ev.meta or {}
        if ev.role == "outcome":
            outcome["events"].append(
                {"at": ev.created_at.isoformat(), "event": ev.text, **meta}
            )
            if "to_stage" in meta:
                outcome["final_stage"] = meta["to_stage"]
            if meta.get("status"):
                outcome["status"] = meta["status"]
            continue
        if ev.role == "tool":
            name = meta.get("tool") or "?"
            tools[name]["calls"] += 1
            if not meta.get("ok", True):
                tools[name]["errors"] += 1
            messages.append(
                {
                    "role": "tool",
                    "tool": name,
                    "args": meta.get("args"),
                    "ok": meta.get("ok", True),
                    "result": ev.text,
                }
            )
            continue
        if ev.role == "user":
            turns += 1
        if ev.lang:
            langs[ev.lang] += 1
        tokens += int((meta.get("tokens") or {}).get("total", 0) or 0)
        messages.append({"role": ev.role, "text": ev.text, "lang": ev.lang})
    first, last = events[0], events[-1]
    return {
        "thread_id": thread_id,
        "tenant": first.tenant_id,
        "channel": first.channel,
        "agent": first.agent,
        "chat_id": first.chat_id,
        "started_at": first.created_at.isoformat(),
        "ended_at": last.created_at.isoformat(),
        "turns": turns,
        "tokens": tokens,
        "languages": dict(langs),
        "tools": {k: v for k, v in tools.items()},
        "outcome": outcome,
        "messages": messages,
    }


def _print_stats(dialogues: list[dict], events: list) -> None:
    roles = Counter(ev.role for ev in events)
    langs: Counter = Counter()
    tools: dict[str, dict] = defaultdict(lambda: {"calls": 0, "errors": 0})
    stages: Counter = Counter()
    tokens = 0
    for d in dialogues:
        langs.update(d["languages"])
        tokens += d["tokens"]
        for name, stat in d["tools"].items():
            tools[name]["calls"] += stat["calls"]
            tools[name]["errors"] += stat["errors"]
        stage = d["outcome"].get("final_stage")
        stages[stage if stage is not None else "—"] += 1

    def block(title: str, rows: list[tuple[str, object]]) -> None:
        print(f"\n{title}")
        print("-" * len(title))
        if not rows:
            print("  (bo'sh)")
        for key, value in rows:
            print(f"  {key:<28} {value}")

    print(f"\nSuhbatlar (threads): {len(dialogues)}    Hodisalar (events): {len(events)}")
    print(f"Jami tokenlar: {tokens:,}")
    block("Hodisa turlari", sorted(roles.items()))
    block("Til / yozuv", sorted(langs.items(), key=lambda kv: -kv[1]))
    block(
        "Tool chaqiruvlari",
        [
            (name, f"{stat['calls']} ta, xato: {stat['errors']}")
            for name, stat in sorted(tools.items(), key=lambda kv: -kv[1]["calls"])
        ],
    )
    block("Yakuniy bosqich (funnel)", sorted(stages.items(), key=lambda kv: str(kv[0])))
    longest = sorted(dialogues, key=lambda d: -d["turns"])[:5]
    block(
        "Eng uzun suhbatlar",
        [(d["thread_id"], f"{d['turns']} navbat") for d in longest],
    )
    print()


async def main_async(args: argparse.Namespace) -> int:
    events = await _load(args.tenant, _parse_since(args.since))
    if not events:
        print("Hech qanday yozuv topilmadi (conversation_events bo'sh).")
        return 0
    dialogues = [
        _dialogue(thread_id, evs) for thread_id, evs in _group(events).items()
    ]
    dialogues = [d for d in dialogues if d["turns"] >= args.min_turns]
    if args.outcome_only:
        dialogues = [d for d in dialogues if d["outcome"]["events"]]

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for d in dialogues:
                fh.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
        print(f"{len(dialogues)} ta suhbat yozildi → {out}")
    if args.stats or not args.out:
        _print_stats(dialogues, events)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", help="faqat shu tenant (masalan: maskan)")
    parser.add_argument("--since", help="ISO sana/vaqt, masalan 2026-08-01")
    parser.add_argument("--out", help="JSONL fayl yo'li (har qator — bitta suhbat)")
    parser.add_argument("--stats", action="store_true", help="statistikani chiqarish")
    parser.add_argument(
        "--min-turns", type=int, default=1, help="shuncha navbatdan kam suhbatlarni tashlab ketish"
    )
    parser.add_argument(
        "--outcome-only",
        action="store_true",
        help="faqat natijasi (stage/status) belgilangan suhbatlar",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
