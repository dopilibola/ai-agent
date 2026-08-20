# AI Sales

Multi-tenant Telegram AI sales agent platform.

Shared core packages live at the repo root and provide the agent runtime, Telegram channels, RAG, voice transcription, operator notifications, and sync loops. Tenants in `apps/` declare their own prompt, tools, and channel bindings — no per-tenant framework code.

```
core/                    Agent, Channel ABC, Tenant, Runtime, context vars
channels/telegram/       Telethon user-account + bot channel adapters
voice/                   Whisper transcription
rag/                     ChromaDB store + CLIP/LiteLLM embedders + search
notifications/           Telegram operator notifier (text, photo, status updates)
sync/                    Background sync-loop runner
db/                      Postgres backend (REQUIRED — set DATABASE_URL): stores,
                         LangGraph checkpointer, Alembic migrations

apps/
├── oygul/               Flower shop — customer agent (Lola) + merchant catalog agent
├── anfa/                Clinic (Anfa, Tashkent) — service-catalog advisor (Excel-fed) + KB sync
├── byd/                 Detox clinic (BYD Medical) — 8-stage sales funnel, deal
│                        scheduler, Bitrix24 mirror, PDF booking voucher
└── maskan/              Grave care (Maskan) — care advisor in front of the existing
                         Flutter app + Django backend; care scheduler + order watcher
```

## Quickstart

```bash
uv sync                                 # install everything
cp .env.example .env                    # fill in credentials

# Postgres is REQUIRED (no JSON/in-memory fallback). The bots run on the host
# (pm2) and reach the container on localhost.
docker compose up -d                    # start Postgres
uv run alembic upgrade head             # create Postgres schema (needs DATABASE_URL set)

# Tenant entrypoints (defined in pyproject.toml)
uv run oygul-customer                   # run Lola on Telegram user account
uv run oygul-merchant                   # run merchant catalog bot
uv run anfa-all                        # run anfa bot + userbot + sync loop
uv run anfa-bot                        # or individually
uv run anfa-userbot
uv run anfa-sync
uv run byd-all                         # byd userbot + operator bot + deal scheduler
uv run maskan-all                      # maskan userbot + operator bot + scheduler + order watcher

# Helper scripts
uv run python scripts/oygul_embed.py --json bouquets.json
uv run python scripts/anfa_telethon_login.py
uv run python scripts/maskan_telethon_login.py
```

## Adding a new tenant

1. Create `apps/<tenant>/`
2. Define `config.py` with envs, `tools.py` with `@tool` functions, and a `prompts/` folder
3. Write an entrypoint that builds a `Tenant(id, agents, channels, sync_jobs)` and hands it to `Runtime`
4. Register a console script in `pyproject.toml`

No changes to the shared core packages should be required for typical tenants.
