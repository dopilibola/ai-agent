# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A multi-tenant Telegram AI sales-agent platform. The repo is split into **shared, tenant-agnostic core packages** (`core/`, `channels/`, `rag/`, `voice/`, `notifications/`, `sync/`, `db/`) and **tenants** under `apps/` that are pure configuration + wiring — a tenant declares its prompt, `@tool` functions, and channel bindings, then hands a `Tenant(id, agents, channels, sync_jobs)` to `Runtime`. Adding or changing a tenant should not require touching the core packages.

Four tenants ship today:

- `apps/oygul/` — flower shop: a customer userbot "Lola" + a merchant catalog bot.
- `apps/anfa/` — Tashkent clinic: a service-catalog advisor on bot+userbot. It searches the clinic's priced service catalog, quotes prices, and directs clients to walk into the clinic. There is **no online booking** — the clinic keeps its own local-only CRM and registers patients in person; it exports its service list as Excel, which the admin panel ingests into Postgres → Chroma.
- `apps/byd/` — BYD Medical detox clinic: a faithful rebuild of the clinic's Bitrix24 sales funnel. A customer userbot (AI sales agent) + an operator bot (manager agent + inline buttons) + a durable per-deal scheduler firing ~23 time-delayed touches across 8 stages, mirrored into Bitrix24 (`bitrix.py`/`bitrix_sync.py`), with a PDF booking voucher (`voucher.py`).
- `apps/maskan/` — Maskan grave care: an AI advisor on a client userbot in front of an **existing Flutter app + Django/DRF backend**. It is the platform's reference for a tenant whose domain data lives in someone else's system — see "The Maskan pattern" below.

Separately, an internal **admin panel** lives in `admin/` (a standalone FastAPI API) and `web/` (a React/Vite UI). It reads/writes the shared Postgres but is **not** part of the tenant runtime — see "Admin panel" under cross-cutting patterns.

Separately, an internal **admin panel** lives in `admin/` (a standalone FastAPI API) and `web/` (a React/Vite UI). It reads/writes the shared Postgres but is **not** part of the tenant runtime — see "Admin panel" under cross-cutting patterns.

## Commands

Python ≥3.12, managed with **uv**.

```bash
uv sync                                  # install all deps into .venv

# Run a tenant (console scripts from pyproject.toml [project.scripts])
uv run oygul-customer                    # Lola on a Telegram user account
uv run oygul-merchant                    # merchant catalog bot
uv run anfa-all                          # anfa bot + userbot + KB sync in one process
uv run anfa-bot | anfa-userbot | anfa-sync   # or each anfa worker alone
uv run python scripts/anfa_import_catalog.py apps/anfa/exports/medplus_export.xlsx   # import the clinic's Excel service export into Postgres (also exposed as an admin-panel upload)
uv run python scripts/anfa_import_doctors.py "apps/anfa/exports/Шифокорлар руйхати.docx"   # import the clinic's Word doctor roster (also an admin-panel upload)
uv run byd-all                           # byd userbot + operator bot + deal scheduler (+ Bitrix sync)
uv run byd-userbot | byd-operator        # or each byd half alone
uv run maskan-all                        # maskan userbot + operator bot + care scheduler + order watcher
uv run maskan-userbot | maskan-operator  # or each maskan half alone

# Helper scripts
uv run python scripts/oygul_embed.py --json bouquets.json   # ingest catalog → CLIP → Chroma
uv run python scripts/anfa_telethon_login.py                # one-time interactive userbot login
uv run python scripts/byd_telethon_login.py                 # (same, per tenant)
uv run python scripts/maskan_telethon_login.py

# Postgres (optional persistence backend — see below)
docker compose up -d                     # Postgres on localhost:54329 (POSTGRES_PORT)
uv run alembic upgrade head              # apply migrations (requires DATABASE_URL set)
uv run alembic revision --autogenerate -m "msg"   # new migration from db/models.py changes

# Production process management (bots run on host, reach Postgres in Docker)
pm2 start ecosystem.config.js            # starts all bots + the admin-api panel
pm2 logs anfa-all

# Admin panel (internal — ONE FastAPI process serves the API + the built UI)
uv sync --extra admin                    # one-time: install FastAPI/uvicorn into .venv
npm install --prefix web && npm run build --prefix web   # build UI -> web/dist (rebuild to update)
uv run --extra admin admin-api           # serves UI + /api on 127.0.0.1:58210 (needs ADMIN_* + DATABASE_URL)
# dev hot-reload (optional): npm run dev --prefix web    # UI on :5173, proxies /api -> :58210
```

There is **no test suite and no configured linter/formatter** — don't go looking for `pytest`/`ruff` config. Verify changes by running the relevant entrypoint.

## Request lifecycle (the central flow)

Read these together — the flow spans `channels/telegram/_telethon_base.py`, `core/channel.py`, `core/agent.py`, and `core/context.py`:

1. A Telegram message arrives. `TelethonChannel` **debounces and batches** rapid messages per chat (`debounce_seconds`), transcribes voice via `voice/`, downloads photos, and builds multimodal user content (text + base64 image blocks). A picture sent **uncompressed ("send as file")** arrives as a *document*, not a photo — those are picked up too (`_is_image_file`, stickers/GIFs excluded), and the data-URL mime is sniffed from the magic bytes since a file keeps its original format. PDFs are still ignored.
2. `Channel.dispatch(chat_id, content)` **publishes three context vars** (`current_tenant_id`, `current_chat_id`, `current_channel`) and then calls `Agent.invoke()`, resetting them in a `finally`.
3. `Agent` wraps LangChain `create_agent` + a LangGraph checkpointer. Conversation state is keyed by `thread_id = "{tenant}:{channel}:{chat_id}"` (see `Channel.thread_id`).
4. **Tools read the live channel and chat from `core.context`**, not from their arguments — that's how `search_bouquets_tool` sends a photo album or a "typing…" status mid-call. When writing a new tool, pull `current_channel`/`current_chat_id` from `core.context`; never expect them as parameters.
5. The final assistant text is returned and the channel sends it.

`Runtime.run_async()` launches every channel and sync job as **independent asyncio tasks** — if one crashes (e.g. an unauthorized userbot session), the others keep running; a per-task watcher logs the failure. Shutdown is on SIGINT/SIGTERM.

## Cross-cutting patterns you must understand before editing

**AI-mute / human handoff.** When a customer pays (oygul) or requests an operator (anfa's `request_operator` tool), a tool calls `mute_store.mute(chat_id)` and notifies operators with an inline **"Подключить ИИ"** button. While a chat is muted, `Channel.dispatch()` returns `""` (no reply) and the channel stays *fully passive* — no read receipts, no typing/online presence — so the human operator silently owns the conversation. An operator clicking the button fires a Telethon `CallbackQuery` handler (registered via `channel.add_callback_handler` in the tenant entrypoint) that calls `unmute()`. **Critical:** the mute is *set* in one process (the customer userbot) but *cleared* in another (the merchant/bot process), so the `MuteStore` must be shared across processes.

**Persistence is Postgres-only — `DATABASE_URL` is required.** There is no JSON / in-memory fallback (it was removed). `PostgresMuteStore`/`PostgresTokenStore` (satisfying the `MuteStore`/`TokenStore` Protocols in `core/`) + `AsyncPostgresSaver` back every tenant process, shared and durable. Each tenant's `services.py` constructs the stores unconditionally; `db.checkpointer_scope()` — which every entrypoint wraps around `Runtime.run_async()` — raises `SystemExit` if `DATABASE_URL` is unset.
- **`db/` imports stay lazy** — sqlalchemy/psycopg are imported inside the functions/methods that use them (mirroring `rag`/`voice`), so importing `db` is cheap and startup doesn't pay for the DB drivers until a store/engine is first used. Keep new `db/` code lazy.
- `db/models.py` holds only shared, tenant-agnostic runtime state (`muted_chats`, `chat_token_usage` + the shared `Base`). Per-tenant **domain** data (catalogue, orders, …) lives in `apps/<tenant>/models.py` + `apps/<tenant>/repository.py` — see "Adding a tenant".

**Admin panel (`admin/` + `web/`).** A standalone FastAPI service (console script `admin-api`, pm2 app `admin-api`) that reads/writes the **same shared Postgres** the bots use — muted chats, per-chat token usage, and conversation checkpoints — and imports none of the agent/Telegram runtime. It **requires `DATABASE_URL`** (like the bots — it reads the same shared Postgres). Its deps live in the `admin` optional-extra (`uv sync --extra admin`), kept out of the base install so a bot-only deploy stays lean. The API is mounted under `/api`, and the **same process serves the built React UI** (`web/dist`, from `npm run build`) at `/` — so the whole panel is one pm2 app on one port (`admin-api`, default `127.0.0.1:8100`). (`npm run dev` + the `/api` Vite proxy gives hot-reload during development.) The tenants the panel knows about are a static registry in `admin/tenants.py` (`TENANTS`). The panel can also **view and edit each agent's system prompt** (a "Prompts" tab, gated by the `has_prompts` capability): the editable `.md` files are a static registry in `admin/prompts.py` — the client only ever sends a `(tenant, key)` pair and the file path is resolved server-side, so edits are bounded to those files (no client-supplied paths). This is the one place the panel writes outside Postgres: it writes the prompt files on disk directly. Every agent loads its prompt via a **callable that re-reads its file on each invoke**, so a saved edit goes live on the running bot without a restart (the `Agent` rebuilds its graph only when the rendered text changes; anfa's catalog prompt additionally substitutes `{now_iso}`/`{weekday}`). This relies on the bots and `admin-api` sharing the host filesystem — they do under pm2. The anfa panel also exposes an **Excel catalog upload** (`POST /api/tenants/anfa/catalog/import`, gated by `has_catalog_import`): it parses the clinic's export and reconciles the `anfa_catalog` Postgres table (the bot's KB sync mirrors it into Chroma on the next tick).

**`/clear` and `/reset`.** Handled in `_telethon_base.py`: these commands wipe the chat's conversation thread (`Agent.clear_thread`) **and** zero its token tally (`Agent.clear_tokens` → `TokenStore.reset`). `reset(chat_id)` is part of the `TokenStore` Protocol, implemented by `PostgresTokenStore`.

**Two distinct RAG strategies share `rag/VectorStore` (a ChromaDB wrapper):**
- **oygul** uses `CLIPEmbedder` (`clip-ViT-B-32`, multimodal): embeddings are computed *externally* and passed as vectors. **Catalog search queries must be in English** — CLIP's text encoder is English-trained, so the agent translates intent to English before searching. Bouquet prices are stored in **tiyin** (`price // 100 = sum`); see `apps/oygul/models.py`. The merchant `add_bouquet_tool` ingests live (the online equivalent of `scripts/oygul_embed.py`): the uploaded photo → **Cloudflare Images** (`apps/oygul/photos.py`, durable public URL) → CLIP embed + Chroma upsert (`BouquetSearch.index`) → a `oygul_bouquets` row via the repository. The merchant channel sets `retain_images=True` so a photo uploaded earlier in the chat is still reachable when the merchant later confirms "save" (per-chat accumulator published on `current_images`).
- **anfa** uses `LiteLLMEmbeddingFunction` (text embeddings) set as Chroma's `embedding_function`, so you pass *texts* and Chroma embeds. One collection holds two doc types — the priced service catalog (`service_*` docs — title + category + tab; price is metadata only) and the doctor roster (`doctor_*` docs — name + speciality + experience; walk-in hours in metadata); `apps/anfa/sync.py` refreshes both incrementally by content hash. Multilingual embeddings let Uzbek queries match the Russian-language catalog and the Uzbek roster.

**Model/provider config.** The chat model is built via langchain `init_chat_model` with `temperature=0`, provider-routed by `CHAT_PROVIDER`/`CHAT_MODEL` (default `groq` + `openai/gpt-oss-20b`). **OpenAI is always required regardless of chat provider** — voice transcription and embeddings go through OpenAI. A `SummarizationMiddleware` compresses history past ~10k tokens. A tenant may pass a *callable* `system_prompt` that is re-rendered every invoke (anfa injects the current clinic-timezone datetime); the agent graph is rebuilt only when the rendered prompt changes.

**The notifier bot is the channel bot.** `notifications/operator.py` fans out to operators over the raw Telegram Bot HTTP API (httpx), while the same bot token's Telethon client (the merchant/bot channel) listens for the unmute button's callbacks. `UNMUTE_CALLBACK_PREFIX` (`unmute:<chat_id>`) is the shared contract between them.

## Config conventions

- Each tenant has a frozen `config.py` dataclass instantiated at import (`config = OygulConfig()`), with `load_dotenv()` called first. Fields resolve **tenant-prefixed env first, then a shared fallback** (e.g. `OYGUL_TG_API_ID` → `TG_API_ID`).
- Entrypoints inject the resolved OpenAI/Groq key into `os.environ` so the litellm/openai SDKs pick it up.
- Per-tenant services in `services.py` are lazy module-level singletons (`get_voice()`, `get_notifier()`, …); tests/overrides monkey-patch those slots.
- `anfa` keeps two Postgres tables in `apps/anfa/models.py` (+ `repository.py`), both fed by clinic exports and reconciled whole on each import (stable ids, so re-imports are incremental): a **flat service catalog** `anfa_catalog` (from the clinic's `.xlsx` — `tab` Прием/Лаборатория/Услуги/Диагностика/Операционный/Группа, `category` speciality-for-Прием, `title`, `price` UZS; parsed by `import_catalog.py`, id = `sha1(tab|category|title)`) and a **doctor roster** `anfa_doctors` (from a `.docx` — name, speciality, experience, weekly `schedule` + a clean `hours_label`; parsed by `import_doctors.py`, id = `sha1(fullname)`). Both are *reference* data — there is no visit/availability data and **no online booking**; the clinic registers patients in person and the roster's hours are shown as walk-in times. Each import runs two ways: an admin-panel upload (`has_catalog_import` / `has_doctors`) and a CLI (`scripts/anfa_import_catalog.py`, `scripts/anfa_import_doctors.py`). The catalog agent's tools (`apps/anfa/tools.py`, `CATALOG_TOOLS`) are `search_services` (→ priced results), `search_doctors` (→ name + experience + walk-in hours), `list_service_categories`, `request_operator` (notifies staff, keeps answering), and `handoff_for_results` — the one true handoff, used *only* when the client wants their lab/analysis results: it notifies moderators with the "Подключить ИИ" button AND mutes the chat so a human sends the results (re-using oygul's callback infra). It needs the client's full name + date of birth for the lab desk to find them; clients often supply both by **sending a photo of their passport**, which the agent reads off the image (see "PHOTOS THE CLIENT SENDS" in `prompts/catalog_system.md` — the prompt must explicitly authorise this or the model refuses ID documents on reflex). Emergency triage (`apps/anfa/triage.py`) still runs as a deterministic `message_guard` before the agent. A short KB sync interval (`ANFA_SYNC_INTERVAL_SECONDS`, default 300s) mirrors both tables into Chroma quickly.
- Telethon is pinned exactly (`telethon==1.43.2`). Sessions live in `data/*.session` (gitignored); a user-account channel fails fast if its session isn't pre-authorized (run the login script once). `mark_read` is silently skipped on bot accounts (Telegram forbids it).

**The durable scheduled-task queue (byd + maskan).** Both funnel tenants run the same engine, and it is the piece to copy for any tenant that needs time-delayed outreach. A `<tenant>_scheduled_tasks` table holds "run `action_type` for lead L at time T" rows; a `SyncJob` poller claims due rows with `FOR UPDATE SKIP LOCKED` (so two processes never double-fire), dispatches each through the funnel's `ACTIONS` registry, and marks it done or retries it with backoff before parking it in `failed`. Enqueue is idempotent via a deterministic `dedup_key`, and `ON CONFLICT` **resets** the row rather than doing nothing — that is what lets a transition cancel-then-re-enqueue a plan, and what lets a chat-silence timer be pushed back by each new inbound message. `reclaim_stale_running` un-sticks rows left by a crashed poller. Executors run out-of-band with **no** `current_channel`/`current_chat_id` context vars set — they reach the live channel and notifier through the funnel's `get_context()`, wired once at startup by the entrypoint. Scheduled touches deliberately bypass the mute gate (`compose_outbound` + `send_text`, not `dispatch`): they are pre-scripted funnel steps, not the AI conversing, so a human operator owning the chat still gets their CRM reminders sent. Each touch is composed **through the agent** (`compose_outbound`) so it lands in the customer's own language, with a verbatim `fallback` template and `must_include` fragments (payment links, addresses) re-appended if the model dropped them.

**The Maskan pattern — a tenant whose domain data lives in another system.** oygul/anfa/byd own their domain tables in this Postgres. `apps/maskan/` is the reference for the other case: Maskan is an existing product (Flutter app + Django/DRF backend at `app.mas-kan.uz`) that already owns the catalogue, the graves, the orders and the money. The tenant therefore stores **only funnel state** (`maskan_leads` + `maskan_scheduled_tasks`) and reaches everything else over HTTP through `apps/maskan/api_client.py`. Three consequences worth internalising before editing it:
- **There is no local price/service table, on purpose.** Duplicating the catalogue would create two prices for one service and only one of them would be what the customer is charged. `create_order` sends service *codes*; the backend resolves the amounts. A model that misremembers a price cannot put a wrong number into a real order.
- **Payment and work status are observed, never asserted.** Payme's webhook marks the order paid; the caretaker's own Telegram workflow moves it to accepted/completed. `apps/maskan/order_watcher.py` (a second `SyncJob`) polls the backend for those changes and calls the matching `funnel.on_*` transition. That is why no tool can mark an order paid. `MaskanLead.last_order_status`/`last_payment_status` record what the client was already told, which is what makes the watcher idempotent.
- **The backend needed a new, purely additive surface.** Maskan's existing `/api/...` is DRF-token auth scoped to `request.user`, and the agent has no user token — only a Telegram `chat_id`. So the Django side gained `backend/botapi/` (a package with **no models**, not in `INSTALLED_APPS`, wired by one line in `maskan/urls.py`): `X-Bot-Key` shared-secret auth (`settings.BOT_API_KEY`; unset ⇒ every route 503s) plus chat_id→user resolution through `accounts.User.telegram_chat_id`, which the existing @Maskanuzbot `/start` flow already populates. It reuses `orders.payme.create_awaiting_order`/`build_checkout_url` rather than re-implementing order creation. `MASKAN_API_KEY` here must equal `BOT_API_KEY` there.
- **Do not add a "link account by phone" endpoint or tool.** It looks like an obvious UX win and it is an account-takeover hole: `telegram_chat_id` is the channel `accounts/password_reset.py` sends **password-reset codes** to, so letting a caller claim a phone number by typing it would redirect a stranger's reset codes — and thus their Maskan app account — to the caller's chat. Nothing in such a request proves phone ownership. Linking therefore stays exclusively in the existing @Maskanuzbot flow, which uses Telegram's own "share contact" button; the agent only ever *reads* the link state (`users/resolve/`) and sends unlinked clients to that bot (`MASKAN_ACCOUNT_BOT_URL`). Both `botapi/views.py` and `apps/maskan/api_client.py` carry a comment at the spot where the endpoint would naturally go.
- Seasonal demand is the real driver: in Uzbekistan graves are visited before Hayit and on Arafa. Those dates are lunar, so `MASKAN_MEMORIAL_DATES` is an admin-maintained ISO date list and the funnel schedules a nudge `MASKAN_MEMORIAL_LEAD_DAYS` before each. The `memorial` action is deliberately **stage-independent** (`_STAGE_INDEPENDENT` in `funnel.py`) — it survives stage transitions, since a client who just paid and a client who went cold both want it.

## Adding a tenant

1. Create `apps/<tenant>/` with `config.py` (env-driven dataclass), `tools.py` (`@tool` functions), `services.py` (singletons + the JSON/Postgres store switch), and `prompts/`.
2. Write an entrypoint that builds `Agent`(s), binds them to `Channel`(s), assembles a `Tenant`, and runs it inside `async with checkpointer_scope() as cp: await Runtime(build_tenant(checkpointer=cp)).run_async()`.
3. Register console script(s) in `pyproject.toml [project.scripts]` and add a pm2 app in `ecosystem.config.js`.
4. If the tenant should show up in the admin panel, add it to `TENANTS` in `admin/tenants.py` (id, display name, channel names).

**Per-tenant domain data (CRM) lives in the tenant, not in `db/` or `admin/`.** A tenant that persists its own domain data (catalogue, orders, bookings) owns two files: `apps/<tenant>/models.py` (SQLAlchemy ORM tables, declared on the *shared* `db.models.Base`) and `apps/<tenant>/repository.py` (all DB reads/writes for that tenant). The same repository serves both the bot write path (tools) and the admin read/manage path. Keep `models.py`/`repository.py` import-light — only `sqlalchemy` + `db.engine` + the tenant's own `models` — so the admin panel can import the repository without dragging in CLIP/telethon/langchain. `db/models.py` stays tenant-agnostic (shared `Base` + `muted_chats`/`chat_token_usage` only). Two wiring steps: declare tables on `db.models.Base`, and `import apps.<tenant>.models` in `db/migrations/env.py` so `alembic revision --autogenerate` sees them. (oygul is the reference implementation: `oygul_bouquets` + `oygul_orders`, migration `0002`.)
