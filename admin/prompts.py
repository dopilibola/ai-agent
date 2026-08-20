"""Static registry of editable agent prompts.

Each tenant ships its agents' system prompts as Markdown files under
``apps/<tenant>/prompts/``. The bots read those files **on every invoke** (the
agent only rebuilds its graph when the rendered text changes), so editing a file
here takes effect on the running bot without a restart — the panel and the bots
share the host filesystem under pm2.

This registry is the *only* source of truth for which files the panel may
read/write. The HTTP layer never accepts a path from the client; it accepts a
``(tenant, key)`` pair and resolves it here, so the edit surface is bounded to
these four files and can't escape the prompts directories.

Keep this in sync when you add a tenant agent with an editable prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

# repo_root/apps/<tenant>/prompts/<file>.md — resolved from this file, not cwd
# (mirrors admin.app._WEB_DIST), so it works regardless of where uvicorn runs.
_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class PromptInfo:
    tenant: str
    key: str          # stable id within the tenant (used in the URL)
    label: str        # human label for the panel
    note: str         # one-line description of the agent/role
    path: Path        # absolute path to the .md file


PROMPTS: dict[str, list[PromptInfo]] = {
    "anfa": [
        PromptInfo(
            tenant="anfa",
            key="catalog",
            label="Catalog advisor (clients)",
            note=(
                "Client-facing service-catalog advisor on the bot + userbot — "
                "quotes prices and invites clients to visit. Fill in the clinic "
                "address/phone/hours block. Keep the {now_iso} and {weekday} "
                "placeholders — they are replaced with the live clinic-timezone "
                "datetime on each reply."
            ),
            path=_REPO_ROOT / "apps" / "anfa" / "prompts" / "catalog_system.md",
        ),
        PromptInfo(
            tenant="anfa",
            key="manager",
            label="Manager agent (clinic staff)",
            note=(
                "Staff-facing admin agent served on the bot to allow-listed "
                "clinic staff (ANFA_MANAGER_ALLOWED_IDS) — manages catalog "
                "prices, the doctor roster, and muted chats over chat."
            ),
            path=_REPO_ROOT / "apps" / "anfa" / "prompts" / "manager_system.md",
        ),
    ],
    "oygul": [
        PromptInfo(
            tenant="oygul",
            key="lola",
            label="Lola — customer sales agent",
            note="Customer-facing salesperson on the userbot.",
            path=_REPO_ROOT / "apps" / "oygul" / "prompts" / "lola_system.md",
        ),
        PromptInfo(
            tenant="oygul",
            key="merchant",
            label="Merchant catalog agent",
            note="Internal merchant bot for adding/managing bouquets.",
            path=_REPO_ROOT / "apps" / "oygul" / "prompts" / "merchant_system.md",
        ),
    ],
    "byd": [
        PromptInfo(
            tenant="byd",
            key="sales",
            label="Sales agent (customers)",
            note=(
                "Customer-facing first-contact sales agent on the userbot — runs "
                "Stage 1 (new lead). Keep the {now_iso} and {weekday} placeholders; "
                "they are replaced with the live clinic-timezone datetime each reply."
            ),
            path=_REPO_ROOT / "apps" / "byd" / "prompts" / "sales_system.md",
        ),
        PromptInfo(
            tenant="byd",
            key="manager",
            label="Operator/manager agent (staff)",
            note="Clinic-staff agent on the operator bot — drives funnel stages by chat.",
            path=_REPO_ROOT / "apps" / "byd" / "prompts" / "manager_system.md",
        ),
    ],
    "maskan": [
        PromptInfo(
            tenant="maskan",
            key="sales",
            label="Dilnoza — care advisor (clients)",
            note=(
                "Client-facing grave-care advisor on the userbot — finds the "
                "cemetery, registers the grave, quotes the live price list and "
                "creates the order with its Payme link. Keep the {now_iso} and "
                "{weekday} placeholders; they are replaced with the live "
                "Tashkent datetime on each reply."
            ),
            path=_REPO_ROOT / "apps" / "maskan" / "prompts" / "sales_system.md",
        ),
        PromptInfo(
            tenant="maskan",
            key="manager",
            label="Operator/manager agent (staff)",
            note=(
                "Maskan-staff agent on the operator bot "
                "(MASKAN_MANAGER_ALLOWED_IDS) — finds cases, shows where they "
                "stand, and closes dead ones."
            ),
            path=_REPO_ROOT / "apps" / "maskan" / "prompts" / "manager_system.md",
        ),
    ],
}


def tenant_has_prompts(tenant_id: str) -> bool:
    return bool(PROMPTS.get(tenant_id))


def list_prompts(tenant_id: str) -> list[PromptInfo]:
    return PROMPTS.get(tenant_id, [])


def get_prompt(tenant_id: str, key: str) -> PromptInfo:
    for p in PROMPTS.get(tenant_id, []):
        if p.key == key:
            return p
    raise HTTPException(
        status_code=404, detail=f"Unknown prompt {key!r} for tenant {tenant_id!r}"
    )


def read_prompt(info: PromptInfo) -> str:
    try:
        return info.path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Prompt file missing on disk: {info.path.name}"
        ) from exc


def write_prompt(info: PromptInfo, content: str) -> int:
    """Overwrite the prompt file. Returns bytes written.

    The directory already exists (it ships with the tenant), so we don't create
    it. Refuses empty content — an empty system prompt would silently break the
    agent.
    """
    if not content.strip():
        raise HTTPException(status_code=422, detail="Prompt content must not be empty.")
    return info.path.write_text(content, encoding="utf-8")
