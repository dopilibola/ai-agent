"""The agent's own rules, editable from the panel.

Each tenant ships its agents' system prompts as Markdown under
``apps/<tenant>/prompts/``. The bots read those files **on every invoke** (an
agent rebuilds its graph only when the rendered text changes), so a save here
reaches the running bot without a restart — the panel and the bots share the
host filesystem.

This registry is the only source of truth for which files the panel may touch.
A request carries a ``(tenant, key)`` pair and the path is resolved here, never
sent by the browser, so the edit surface cannot escape these directories.

Mirrors ``admin/prompts.py`` (the FastAPI panel's registry). Duplicated rather
than imported because that module raises ``fastapi.HTTPException`` and this
panel deliberately depends on neither FastAPI nor the bot runtime — the same
convention the mirrored models follow. **When you add a prompt to one, add it to
the other.**

Every save keeps the previous version under ``prompts/.history/`` — a bad prompt
edit is the fastest way to break a working sales agent, and the file on disk is
otherwise the only copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# <repo>/apps/<tenant>/prompts/<file>.md — resolved from this file, not from the
# working directory, so it holds however the panel process was started.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# How many previous versions of each prompt to keep. Enough to undo a bad
# afternoon; small enough that the directory stays readable.
HISTORY_KEEP = 10


@dataclass(frozen=True)
class PromptInfo:
    tenant: str
    key: str      # stable id within the tenant (used in the URL)
    label: str    # what the panel calls it
    note: str     # one line on what this agent does
    path: Path

    @property
    def history_dir(self) -> Path:
        return self.path.parent / ".history"

    @property
    def exists(self) -> bool:
        return self.path.is_file()


PROMPTS: dict[str, list[PromptInfo]] = {
    "maskan": [
        PromptInfo(
            tenant="maskan",
            key="sales",
            label="Мадамин — mijozlar bilan ishlaydigan agent",
            note=(
                "Mijoz bilan gaplashadigan asosiy agent: qabristonni topadi, "
                "qabrni ro‘yxatga oladi, narxni katalogdan aytadi, buyurtma "
                "ochadi. {now_iso} va {weekday} belgilariga tegmang — ular har "
                "javobda Toshkent vaqtiga almashtiriladi."
            ),
            path=REPO_ROOT / "apps" / "maskan" / "prompts" / "sales_system.md",
        ),
        PromptInfo(
            tenant="maskan",
            key="manager",
            label="Operator agenti (xodimlar uchun)",
            note=(
                "Operator botidagi xodimlar agenti "
                "(MASKAN_MANAGER_ALLOWED_IDS): murojaatlarni topadi, holatini "
                "ko‘rsatadi, keraksizini yopadi."
            ),
            path=REPO_ROOT / "apps" / "maskan" / "prompts" / "manager_system.md",
        ),
    ],
    "anfa": [
        PromptInfo(
            tenant="anfa",
            key="catalog",
            label="Katalog maslahatchisi (mijozlar)",
            note="Klinika xizmatlari va narxlarini aytadigan agent.",
            path=REPO_ROOT / "apps" / "anfa" / "prompts" / "catalog_system.md",
        ),
        PromptInfo(
            tenant="anfa",
            key="manager",
            label="Menejer agenti (klinika xodimlari)",
            note="Narxlar, shifokorlar ro‘yxati va mute chatlarni boshqaradi.",
            path=REPO_ROOT / "apps" / "anfa" / "prompts" / "manager_system.md",
        ),
    ],
    "oygul": [
        PromptInfo(
            tenant="oygul",
            key="lola",
            label="Lola — sotuvchi agent",
            note="Mijozlar bilan ishlaydigan gul sotuvchi.",
            path=REPO_ROOT / "apps" / "oygul" / "prompts" / "lola_system.md",
        ),
        PromptInfo(
            tenant="oygul",
            key="merchant",
            label="Katalog boti (ichki)",
            note="Buketlarni qo‘shish/boshqarish uchun ichki bot.",
            path=REPO_ROOT / "apps" / "oygul" / "prompts" / "merchant_system.md",
        ),
    ],
}


def tenants_with_prompts() -> list[str]:
    return [t for t, items in PROMPTS.items() if items]


def list_prompts(tenant: str) -> list[PromptInfo]:
    return PROMPTS.get(tenant, [])


def get_prompt(tenant: str, key: str) -> PromptInfo | None:
    for info in PROMPTS.get(tenant, []):
        if info.key == key:
            return info
    return None


def read_prompt(info: PromptInfo) -> str:
    try:
        return info.path.read_text(encoding="utf-8")
    except OSError:
        return ""


def history(info: PromptInfo) -> list[tuple[str, str]]:
    """Saved previous versions, newest first: (file name, readable timestamp)."""
    if not info.history_dir.is_dir():
        return []
    files = sorted(
        (p for p in info.history_dir.glob(f"{info.key}-*.md") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    out = []
    for p in files[:HISTORY_KEEP]:
        stamp = p.stem[len(info.key) + 1:]
        try:
            when = datetime.strptime(stamp, "%Y%m%d-%H%M%S").strftime("%d.%m.%Y %H:%M")
        except ValueError:
            when = stamp
        out.append((p.name, when))
    return out


def read_history(info: PromptInfo, name: str) -> str:
    """One saved version, addressed by file name.

    The name is checked against the listing rather than joined blindly, so a
    crafted `name` cannot walk out of the history directory.
    """
    if name not in {n for n, _ in history(info)}:
        return ""
    try:
        return (info.history_dir / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def write_prompt(info: PromptInfo, content: str) -> tuple[bool, str]:
    """Save a prompt, keeping the previous version. Returns (ok, message).

    Refuses empty content: an empty system prompt does not fail loudly, it makes
    the agent quietly generic — the worst kind of breakage to diagnose.
    """
    content = (content or "").replace("\r\n", "\n")
    if not content.strip():
        return False, "Matn bo‘sh bo‘lishi mumkin emas."
    previous = read_prompt(info)
    if previous == content:
        return True, "O‘zgarish yo‘q."
    try:
        if previous:
            info.history_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            (info.history_dir / f"{info.key}-{stamp}.md").write_text(
                previous, encoding="utf-8"
            )
            _trim_history(info)
        info.path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return False, f"Faylni yozib bo‘lmadi: {exc}"
    return True, "Saqlandi — ishlayotgan bot keyingi javobidayoq shu qoidalar bilan ishlaydi."


def _trim_history(info: PromptInfo) -> None:
    files = sorted(info.history_dir.glob(f"{info.key}-*.md"), key=lambda p: p.name)
    for old in files[:-HISTORY_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
