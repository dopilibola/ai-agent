"""Import the clinic's Word doctor roster into the anfa doctors table (Postgres).

The clinic supplied its physician list as a .docx (table: № | FISH |
Mutahassislik | Toifa va staj | Qabul vaqtlari). This loads such a file,
reconciling the whole roster against it. The same import path is also exposed in
the admin panel (file upload); this script is for ops / local loads.

Usage:
    uv run python scripts/anfa_import_doctors.py "apps/anfa/exports/Шифокорлар руйхати.docx"

Requires DATABASE_URL. The KB sync loop mirrors the roster into the vector DB
on its next tick.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv


async def _main(path: Path) -> None:
    from apps.anfa.import_doctors import import_docx_bytes

    summary = await import_docx_bytes(path.read_bytes())
    print(
        f"Imported {path.name}: parsed={summary['parsed']} "
        f"added={summary['added']} updated={summary['updated']} "
        f"removed={summary['removed']} total in roster={summary['total']}"
    )


def run() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import a Word doctor roster into the anfa doctors table.")
    parser.add_argument("docx", type=Path, help="path to the .docx roster")
    args = parser.parse_args()
    if not args.docx.is_file():
        sys.exit(f"File not found: {args.docx}")
    asyncio.run(_main(args.docx))


if __name__ == "__main__":
    run()
