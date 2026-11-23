"""Seed / update the Maskan catalogue: services and cemeteries.

Standalone mode means this tenant owns what it sells, so these two tables are
the source of truth for every price the agent says out loud and every cemetery
it will accept an order for. Both are reconciled by natural key (service `code`,
cemetery `name_uz`), so re-running is safe and only changes what you changed.

    uv run python scripts/maskan_seed_catalog.py                 # apply defaults
    uv run python scripts/maskan_seed_catalog.py --list          # show current
    uv run python scripts/maskan_seed_catalog.py --services my_prices.csv
    uv run python scripts/maskan_seed_catalog.py --cemeteries my_cemeteries.csv
    uv run python scripts/maskan_seed_catalog.py --services p.csv --replace   # to'liq almashtirish

CSV formats (header row required, UTF-8):
    services   : code,name_uz,name_ru,desc_uz,desc_ru,price,sort
    cemeteries : name_uz,name_ru,city,district

The built-in defaults are the four packages the business actually sells
(Ekonom / Bir martalik / Oylik / Yillik) plus a starter set of Tashkent-area
cemeteries. **Check both against reality before selling** — a wrong price here
is a wrong price charged.

Requires DATABASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

# code, name_uz, name_ru, desc_uz, desc_ru, price (so'm), sort
#
# Maskan paket sotadi, alohida xizmat emas: mijoz to'rttadan BITTASINI tanlaydi,
# ularni qo'shib hisoblanmaydi. Narxlar biznesning haqiqiy narx ro'yxati
# (2026-09-15 da yangilandi: Ekonom qo'shildi, Yillik 3 360 000 -> 2 100 000).
#
# Ekonom SHARTLI: u faqat so'nggi 1 oy ichida tozalangan qabr uchun yaroqli —
# sharti tavsifda yozilgan va `tools.CONDITIONAL_SERVICE_CODES` da qayd etilgan,
# shuning uchun agent uni "arzon kirish varianti" sifatida o'zidan taklif qilmaydi.
DEFAULT_SERVICES = [
    (
        "pack_econom", "Ekonom", "Эконом",
        "1 tashrif. Qabr toshi va asosiy yuzani tozalash, o'tlarni yulish, "
        "foto-hisobot (1 ta umumiy rasm). Ushbu tarif so'nggi 1 oy ichida "
        "tozalangan qabrlar uchun mo'ljallangan.",
        "1 визит. Очистка надгробия и основной поверхности, прополка, "
        "фото-отчёт (1 общий снимок). Тариф рассчитан на могилы, убранные "
        "в течение последнего месяца.",
        179000, 10,
    ),
    (
        "pack_once", "Bir martalik", "Разовый",
        "1 tashrif. Qabr toshi, panjara va atrofni chuqur tozalash, o't va "
        "axlatni yig'ib hududdan chiqarib yuborish, to'liq foto-hisobot "
        "(oldin/keyin).",
        "1 визит. Глубокая уборка надгробия, ограды и территории вокруг, "
        "сбор и вывоз травы и мусора, полный фото-отчёт (до/после).",
        280000, 20,
    ),
    (
        "pack_monthly", "Oylik", "Месячный",
        "Oyiga 4 marta tashrif. Har safar to'liq tozalash, obodonlashtirish "
        "va foto-hisobot.",
        "4 визита в месяц. Каждый раз полная уборка, благоустройство и "
        "фото-отчёт.",
        900000, 30,
    ),
    (
        "pack_yearly", "Yillik", "Годовой",
        "Yiliga 12 marta tashrif: 1-tashrif to'liq (Bir martalik darajasida) "
        "tozalash, qolgan 11 oy Ekonom darajasida muntazam parvarish. Har "
        "tashrifdan keyin foto-hisobot.",
        "12 визитов в год: первый визит — полная уборка (уровень Разового), "
        "остальные 11 месяцев — регулярный уход уровня Эконом. Фото-отчёт "
        "после каждого визита.",
        2100000, 40,
    ),
]

# name_uz, name_ru, city, district — Tashkent city + Tashkent region only, which
# is exactly the service area rule: what is not here cannot be sold.
DEFAULT_CEMETERIES = [
    ("Chig'atoy qabristoni", "Кладбище Чигатай", "Toshkent shahri", "Shayxontohur"),
    ("Minor qabristoni", "Кладбище Минор", "Toshkent shahri", "Yunusobod"),
    ("Bo'zsuv qabristoni", "Кладбище Бозсу", "Toshkent shahri", "Yashnobod"),
    ("Do'mbrobod qabristoni", "Кладбище Домбрабад", "Toshkent shahri", "Chilonzor"),
    ("Kamolon qabristoni", "Кладбище Камолон", "Toshkent shahri", "Shayxontohur"),
    ("Qo'yliq qabristoni", "Кладбище Куйлюк", "Toshkent shahri", "Bektemir"),
    ("Sag'bon qabristoni", "Кладбище Сагбан", "Toshkent shahri", "Uchtepa"),
    ("Zangiota qabristoni", "Кладбище Зангиата", "Toshkent viloyati", "Zangiota tumani"),
    ("Qibray qabristoni", "Кладбище Кибрай", "Toshkent viloyati", "Qibray tumani"),
    ("Yangiyo'l qabristoni", "Кладбище Янгиюль", "Toshkent viloyati", "Yangiyo'l tumani"),
    ("Chirchiq qabristoni", "Кладбище Чирчик", "Toshkent viloyati", "Chirchiq shahri"),
    ("Ohangaron qabristoni", "Кладбище Ахангаран", "Toshkent viloyati", "Ohangaron tumani"),
]


def _read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [row for row in csv.DictReader(fh)]


async def _seed_services(rows: list[tuple] | list[dict]) -> list[str]:
    from apps.maskan.repository import get_repository

    repo = get_repository()
    seen: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            code = (row.get("code") or "").strip()
            if not code:
                continue
            fields = dict(
                name_uz=(row.get("name_uz") or "").strip(),
                name_ru=(row.get("name_ru") or "").strip(),
                desc_uz=(row.get("desc_uz") or "").strip(),
                desc_ru=(row.get("desc_ru") or "").strip(),
                price=int(float(row.get("price") or 0)),
                sort=int(float(row.get("sort") or 100)),
                active=True,
            )
        else:
            code, name_uz, name_ru, desc_uz, desc_ru, price, sort = row
            fields = dict(
                name_uz=name_uz, name_ru=name_ru, desc_uz=desc_uz,
                desc_ru=desc_ru, price=price, sort=sort, active=True,
            )
        await repo.upsert_service(code, **fields)
        seen.append(code)
    return seen


async def _seed_cemeteries(rows: list[tuple] | list[dict]) -> list[str]:
    from apps.maskan.repository import get_repository

    repo = get_repository()
    seen: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            name_uz = (row.get("name_uz") or "").strip()
            if not name_uz:
                continue
            fields = dict(
                name_ru=(row.get("name_ru") or "").strip(),
                city=(row.get("city") or "").strip(),
                district=(row.get("district") or "").strip(),
                active=True,
            )
        else:
            name_uz, name_ru, city, district = row
            fields = dict(name_ru=name_ru, city=city, district=district, active=True)
        await repo.upsert_cemetery(name_uz, **fields)
        seen.append(name_uz)
    return seen


async def _retire_services(keep: list[str]) -> int:
    """Deactivate every service the import did not mention.

    Without this an import only ever *adds*: a price list that dropped a service
    would keep selling it, and a stale row is a price the agent will quote.
    Deactivated, not deleted — orders hold a price snapshot and must stay
    readable.
    """
    from apps.maskan.repository import get_repository

    repo = get_repository()
    stale = [s for s in await repo.list_services() if s.code not in keep]
    for svc in stale:
        await repo.upsert_service(svc.code, active=False)
    return len(stale)


async def _retire_cemeteries(keep: list[str]) -> int:
    """Same for cemeteries — an out-of-area name must stop being quotable."""
    from apps.maskan.repository import get_repository

    repo = get_repository()
    rows = await repo.search_cemeteries("", limit=1000)
    stale = [c for c in rows if c.name_uz not in keep]
    for cem in stale:
        await repo.upsert_cemetery(cem.name_uz, active=False)
    return len(stale)


async def _show() -> None:
    from apps.maskan.repository import get_repository

    repo = get_repository()
    services = await repo.list_services()
    cemeteries = await repo.search_cemeteries("", limit=200)
    print(f"\nXizmatlar ({len(services)}):")
    for s in services:
        print(f"  {s.code:<10} {s.name_uz:<24} {s.price:>9,} so'm".replace(",", " "))
    print(f"\nQabristonlar ({len(cemeteries)}):")
    for c in cemeteries:
        where = " / ".join(p for p in (c.city, c.district) if p)
        print(f"  [{c.id:>3}] {c.name_uz:<28} {where}")
    print()


async def main_async(args: argparse.Namespace) -> int:
    from db.engine import database_configured

    if not database_configured():
        raise SystemExit("DATABASE_URL is not set.")

    if args.list:
        await _show()
        return 0

    services = _read_csv(args.services) if args.services else DEFAULT_SERVICES
    cemeteries = _read_csv(args.cemeteries) if args.cemeteries else DEFAULT_CEMETERIES

    if not args.cemeteries_only:
        seen = await _seed_services(services)
        print(f"Xizmatlar yozildi: {len(seen)}")
        if args.replace:
            print(f"  eskilari o'chirildi (nofaol): {await _retire_services(seen)}")
    if not args.services_only:
        seen = await _seed_cemeteries(cemeteries)
        print(f"Qabristonlar yozildi: {len(seen)}")
        if args.replace:
            print(f"  eskilari o'chirildi (nofaol): {await _retire_cemeteries(seen)}")
    await _show()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", help="xizmatlar CSV fayli")
    parser.add_argument("--cemeteries", help="qabristonlar CSV fayli")
    parser.add_argument("--services-only", action="store_true")
    parser.add_argument("--cemeteries-only", action="store_true")
    parser.add_argument("--list", action="store_true", help="hozirgi holatni ko'rsatish")
    parser.add_argument(
        "--replace", action="store_true",
        help="importda yo'q satrlarni nofaol qilish (to'liq almashtirish)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
