"""What the conversation corpus says about the agent — the weekly read.

Everything here comes from `conversation_events`; nothing is labelled by hand.
The point is to turn "the bot feels off" into a ranked list of concrete defects:

    uv run python scripts/corpus_report.py --tenant maskan
    uv run python scripts/corpus_report.py --tenant maskan --since 7d
    uv run python scripts/corpus_report.py --tenant maskan --gold out.jsonl

Sections:

  Funnel      how far conversations get, and where they die
  Tools       which tool fails, how often, with a real error string
  Handoffs    why the agent gave up, grouped — this is the defect backlog
  Gold pairs  what the operator said after a handoff: the reply the agent
              should have produced. Exported with `--gold`
  Prompts     the same numbers split by prompt_version, so an edit can be judged

Requires DATABASE_URL. Read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

STAGE_NAMES = {
    "1": "yangi", "2": "qabriston aniqlandi", "3": "narx aytildi",
    "4": "buyurtma ochildi", "5": "to'landi", "6": "ish qabul qilindi",
    "7": "yakunlandi", "8": "yopildi",
}


def parse_since(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    m = re.fullmatch(r"(\d+)([hdw])", value.strip())
    if not m:
        raise SystemExit("--since formati: 7d, 24h, 2w")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    return datetime.now(timezone.utc) - delta


def connect():
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL o'rnatilmagan.")
    return psycopg.connect(dsn.replace("postgresql+psycopg", "postgresql"))


def normalise_reason(text: str) -> str:
    """Collapse a free-text handoff reason to a groupable key.

    The agent writes the reason itself, so the same defect arrives worded a
    dozen ways. Names, numbers and quotes are the parts that vary; strip them
    and near-duplicates land in one bucket without needing embeddings.
    """
    t = (text or "").lower()
    t = re.sub(r"[\"'«»]", " ", t)
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"[^\w\s#]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    words = [w for w in t.split() if len(w) > 3][:6]
    return " ".join(words) or t[:40]


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * 64)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", default="maskan")
    ap.add_argument("--since", help="masalan 7d / 24h / 2w")
    ap.add_argument("--gold", help="gold juftliklarni shu JSONL faylga yoz")
    ap.add_argument("--limit-examples", type=int, default=3)
    args = ap.parse_args()

    since = parse_since(args.since)
    where = "where tenant_id = %s"
    params: list = [args.tenant]
    if since:
        where += " and created_at >= %s"
        params.append(since)

    with connect() as conn:
        cur = conn.cursor()

        cur.execute(f"""
            select count(*), count(distinct thread_id)
            from conversation_events {where}
        """, params)
        rows, threads = cur.fetchone()
        print(f"\ntenant={args.tenant}  satr={rows}  suhbat={threads}"
              + (f"  davr={args.since}" if args.since else ""))
        if not rows:
            print("\nKorpus bo'sh — hali yozilgan suhbat yo'q.")
            return 0

        # ----- funnel ------------------------------------------------------
        section("Voronka — suhbatlar qayerga yetgan")
        cur.execute(f"""
            select thread_id, max((meta->>'to_stage')::int) as best
            from conversation_events {where} and role = 'outcome'
              and meta->>'to_stage' is not null
            group by thread_id
        """, params)
        reached = cur.fetchall()
        if reached:
            hist = Counter(str(b) for _, b in reached)
            total = len(reached)
            for stage in sorted(hist, key=int):
                n = hist[stage]
                bar = "█" * max(1, round(28 * n / total))
                print(f"  {stage}. {STAGE_NAMES.get(stage, ''):22} {n:>3}  {bar}")
            paid = sum(n for s, n in hist.items() if int(s) >= 5)
            print(f"\n  to'lovgacha yetgan: {paid}/{total} ({100*paid/total:.0f}%)")
        else:
            print("  hali voronka yorlig'i yo'q")

        # ----- tools -------------------------------------------------------
        section("Tool'lar — nima ishlamayapti")
        cur.execute(f"""
            select meta->>'tool',
                   count(*),
                   count(*) filter (where (meta->>'ok')::boolean is false)
            from conversation_events {where} and role = 'tool'
            group by 1 order by 3 desc, 2 desc
        """, params)
        tool_rows = cur.fetchall()
        if tool_rows:
            print(f"  {'tool':18} {'chaqiruv':>8} {'xato':>6}  {'xato %':>7}")
            for name, n, bad in tool_rows:
                pct = 100 * bad / n if n else 0
                flag = "  ⚠" if pct >= 20 and bad else ""
                print(f"  {str(name):18} {n:>8} {bad:>6}  {pct:>6.0f}%{flag}")
            cur.execute(f"""
                select meta->>'tool', text from conversation_events {where}
                  and role = 'tool' and (meta->>'ok')::boolean is false
                order by id desc limit %s
            """, params + [args.limit_examples])
            failures = cur.fetchall()
            if failures:
                print("\n  so'nggi xatolar:")
                for name, text in failures:
                    print(f"    {name}: {str(text)[:110]}")
        else:
            print("  tool chaqiruvi yo'q")

        # ----- handoffs ----------------------------------------------------
        section("Handoff sabablari — bu sizning bug backlog'ingiz")
        cur.execute(f"""
            select meta->>'args', thread_id, created_at from conversation_events {where}
              and role = 'tool' and meta->>'tool' = 'call_human'
            order by id desc
        """, params)
        handoffs = cur.fetchall()
        if handoffs:
            buckets: dict[str, list] = defaultdict(list)
            originals: dict[str, str] = {}
            for raw_args, thread, at in handoffs:
                # The *reason* is the argument the agent passed, not the tool's
                # result — grouping on the result would just count "success".
                try:
                    reason = (json.loads(raw_args or "{}") or {}).get("reason", "")
                except Exception:
                    reason = str(raw_args or "")
                key = normalise_reason(reason)
                buckets[key].append((thread, at))
                originals.setdefault(key, reason)
            for key, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
                print(f"  {len(items):>3}×  {originals.get(key, key)[:70]}")
            print(f"\n  jami handoff: {len(handoffs)} ({100*len(handoffs)/max(threads,1):.0f}% suhbatda)")
        else:
            print("  handoff yo'q")

        # ----- gold pairs --------------------------------------------------
        section("Gold juftliklar — operator nima deganini AI o'rganadi")
        cur.execute(f"""
            select thread_id, id, text, created_at from conversation_events {where}
              and role = 'operator' order by id
        """, params)
        operator_msgs = cur.fetchall()
        print(f"  operator xabarlari: {len(operator_msgs)}")
        if operator_msgs and args.gold:
            pairs = []
            for thread, ev_id, reply, at in operator_msgs:
                cur.execute("""
                    select role, text from conversation_events
                    where thread_id = %s and id < %s and role in ('user','assistant')
                    order by id desc limit 6
                """, (thread, ev_id))
                context = [{"role": r, "text": t} for r, t in reversed(cur.fetchall())]
                pairs.append({
                    "tenant": args.tenant,
                    "thread_id": thread,
                    "context": context,
                    "ai_attempt": next(
                        (c["text"] for c in reversed(context) if c["role"] == "assistant"), ""
                    ),
                    "human_reply": reply,
                    "at": at.isoformat() if at else None,
                    "approved": None,
                })
            out = Path(args.gold)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", encoding="utf-8") as fh:
                for pair in pairs:
                    fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
            print(f"  → {len(pairs)} juftlik yozildi: {out}")
        elif operator_msgs:
            print("  (--gold fayl.jsonl bilan eksport qiling)")

        # ----- prompts -----------------------------------------------------
        section("Prompt versiyalari")
        cur.execute(f"""
            select meta->>'prompt_version', count(*),
                   round(avg((meta->'tokens'->>'total')::numeric)) as avg_tokens,
                   round(avg((meta->>'latency_ms')::numeric)) as avg_ms
            from conversation_events {where} and role = 'assistant'
            group by 1 order by 2 desc
        """, params)
        for version, n, tokens, ms in cur.fetchall():
            label = version or "(versiyasiz — eski satrlar)"
            print(f"  {label:26} javob={n:>4}  o'rt.token={tokens or '-':>6}  o'rt.latency={ms or '-':>6} ms")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
