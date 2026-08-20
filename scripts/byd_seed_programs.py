"""Seed the BYD detox programs (7 / 14 / 21 day) and their prices.

Idempotent upsert — safe to re-run. Prices are placeholders (UZS sum); edit them
here or later via the manager agent's `set_program_price` tool. Requires
DATABASE_URL.

Usage:
    uv run python scripts/byd_seed_programs.py
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv()

from apps.byd.repository import get_repository  # noqa: E402

# (code, title, days, full price in UZS sum) — adjust to real pricing.
PROGRAMS = [
    ("7", "Программа очищения — 7 дней", 7, 7_000_000),
    ("14", "Программа очищения — 14 дней", 14, 13_000_000),
    ("21", "Программа очищения — 21 день", 21, 18_000_000),
]


async def main() -> None:
    repo = get_repository()
    for code, title, days, price in PROGRAMS:
        await repo.upsert_program(code=code, title=title, days=days, price=price)
        print(f"seeded program {code}: {title} — {price:,} сум".replace(",", " "))
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
