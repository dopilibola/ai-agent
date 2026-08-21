"""Review the answers operators gave, and approve the ones worth teaching.

This is the human gate on the only loop that changes the agent's behaviour
automatically. Nothing an operator writes reaches a customer-facing prompt until
someone approves it here.

    uv run python scripts/gold_review.py --tenant maskan --harvest   # corpus -> pending
    uv run python scripts/gold_review.py --tenant maskan             # review pending
    uv run python scripts/gold_review.py --tenant maskan --list
    uv run python scripts/gold_review.py --tenant maskan --embed     # index approved rows

Review keys: [y] approve  [n] reject  [s] skip  [q] quit.
Prices are already masked as {narx} when the pair is harvested — an example
teaches wording, never a figure.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.learning import example_store  # noqa: E402


def connect():
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL o'rnatilmagan.")
    return psycopg.connect(dsn.replace("postgresql+psycopg", "postgresql"))


def show_counts(tenant: str) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            select coalesce(approved::text, 'kutilmoqda'), count(*)
            from learning_examples where tenant_id = %s group by 1 order by 1
            """,
            (tenant,),
        )
        rows = dict(cur.fetchall())
    label = {"true": "tasdiqlangan", "false": "rad etilgan", "kutilmoqda": "ko'rilmagan"}
    print("  " + "  ".join(f"{label.get(k, k)}: {v}" for k, v in rows.items()) or "  bo'sh")


async def review(tenant: str) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            select id, question, ai_attempt, human_reply
            from learning_examples
            where tenant_id = %s and approved is null order by id
            """,
            (tenant,),
        )
        pending = cur.fetchall()
        if not pending:
            print("Ko'rish uchun yangi juftlik yo'q.")
            return
        print(f"{len(pending)} ta juftlik. [y] tasdiq  [n] rad  [s] o'tkazib yubor  [q] chiqish\n")
        for row_id, question, ai_attempt, human_reply in pending:
            print("─" * 68)
            print(f"MIJOZ : {question.strip()[:400]}")
            print(f"AI    : {(ai_attempt or '—').strip()[:400]}")
            print(f"XODIM : {human_reply.strip()[:600]}")
            answer = input("\n  [y/n/s/q] > ").strip().lower()
            if answer == "q":
                break
            if answer == "s":
                continue
            cur.execute(
                "update learning_examples set approved = %s, reviewed_at = %s where id = %s",
                (answer == "y", datetime.now(timezone.utc), row_id),
            )
            conn.commit()
            print("  ✓ tasdiqlandi\n" if answer == "y" else "  ✗ rad etildi\n")


async def embed_approved(tenant: str) -> None:
    """Fill in embeddings for approved rows that lack one."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            select id, question from learning_examples
            where tenant_id = %s and approved is true and embedding is null
            """,
            (tenant,),
        )
        rows = cur.fetchall()
        if not rows:
            print("Embedding kerak bo'lgan satr yo'q.")
            return
        done = 0
        for row_id, question in rows:
            vector = await example_store.embed(question)
            if vector is None:
                print("EMBED_MODEL sozlanmagan yoki o'lchov mos emas — "
                      "trigram qidiruvi bilan ishlayveradi.")
                return
            cur.execute(
                "update learning_examples set embedding = %s::vector where id = %s",
                (vector, row_id),
            )
            done += 1
        conn.commit()
        print(f"{done} ta satr indekslandi.")


async def list_all(tenant: str) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            select id, approved, left(question, 60), left(human_reply, 70)
            from learning_examples where tenant_id = %s order by id
            """,
            (tenant,),
        )
        for row_id, approved, question, reply in cur.fetchall():
            mark = {True: "✓", False: "✗", None: "·"}[approved]
            print(f"{mark} [{row_id:>4}] {question.strip():62} → {reply.strip()}")


async def main_async(args) -> int:
    if args.harvest:
        added = await example_store.harvest(args.tenant)
        print(f"Korpusdan olindi: {added} ta yangi juftlik")
    if args.list:
        await list_all(args.tenant)
    elif args.embed:
        await embed_approved(args.tenant)
    elif not args.harvest or args.review:
        await review(args.tenant)
    print()
    show_counts(args.tenant)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", default="maskan")
    ap.add_argument("--harvest", action="store_true", help="korpusdan yangi juftliklarni ol")
    ap.add_argument("--review", action="store_true", help="harvest'dan keyin ham ko'rib chiqish")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--embed", action="store_true", help="tasdiqlanganlarni indeksla")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
