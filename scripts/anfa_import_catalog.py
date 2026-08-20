"""Import the clinic's Excel service export into the anfa catalog (Postgres).

The clinic exports its full service list as an .xlsx (one `services` sheet with
columns tab, category_name, title, price) and re-exports on every change. This
loads such a file, reconciling the whole catalog against it. The same import
path is also exposed in the admin panel (file upload); this script is for ops /
local loads.

Usage:
    uv run python scripts/anfa_import_catalog.py apps/anfa/exports/medplus_export.xlsx

Requires DATABASE_URL (the catalog lives in the shared Postgres). The KB sync
loop mirrors the new catalog into the vector DB on its next tick.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv


async def _main(path: Path) -> None:
    from apps.anfa.import_catalog import import_workbook_bytes

    data = path.read_bytes()
    summary = await import_workbook_bytes(data)
    print(
        f"Imported {path.name}: parsed={summary['parsed']} "
        f"added={summary['added']} updated={summary['updated']} "
        f"removed={summary['removed']} total in catalog={summary['total']}"
    )


def run() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import an Excel service export into the anfa catalog.")
    parser.add_argument("xlsx", type=Path, help="path to the .xlsx export")
    args = parser.parse_args()
    if not args.xlsx.is_file():
        sys.exit(f"File not found: {args.xlsx}")
    asyncio.run(_main(args.xlsx))


if __name__ == "__main__":
    run()
