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

## Admin panels

Two of them, over the **same** Postgres, and neither is part of the tenant runtime:

| | `admin/` + `web/` (`admin-api`) | `django_admin/` (`django-panel`) |
|---|---|---|
| Stack | FastAPI + React (Vite) | Django admin |
| Extra | `uv sync --extra admin` | `uv sync --extra panel` |
| Built for | chats, token usage, **prompt editing**, anfa Excel/Word imports | every table as a CRM: funnels, orders, price lists, the corpus |
| Default port | `127.0.0.1:8100` | `127.0.0.1:8200` |

Alembic remains the single owner of the shared schema. The Django panel maps the
existing tables as **unmanaged** models and keeps Django's own tables (users,
sessions, admin log) in a local SQLite file, so `manage.py migrate` can never
touch the bots' database.

```bash
uv sync --extra panel
uv run --extra panel python django_admin/manage.py migrate          # SQLite only
uv run --extra panel python django_admin/manage.py createsuperuser
uv run --extra panel django-panel                                   # 127.0.0.1:8200

# dev, with tracebacks and no HTTPS cookie requirement:
DJANGO_DEBUG=1 uv run --extra panel python django_admin/manage.py runserver
```

Env it reads (all optional except the first two, which the bots already set):

- `DATABASE_URL` — the shared Postgres; `postgresql+psycopg://…` is accepted.
- `DJANGO_SECRET_KEY` — **required** unless `DJANGO_DEBUG=1`.
- `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` — set both when it sits behind nginx.
- `DJANGO_PANEL_HOST` / `DJANGO_PANEL_PORT`, `DJANGO_PANEL_DB` (the SQLite path),
  `DJANGO_LANGUAGE_CODE`, `TZ_NAME`.

### The pages

Beyond a model admin for all 21 tables, the panel has five pages of its own
(`django_admin/panel/views.py`, aggregated in `django_admin/crm/analytics.py`,
charts drawn server-side as inline SVG by `panel/charts.py` — no CDN, no JS).

They are organised around one number: **`crm/scoring.py` gives every chat a buy
probability** (0-100), computed from named signals — how far the funnel got,
how much they wrote, whether they left a phone number, whether they answered our
last message — and decayed by how long they have been silent. It is explainable
(the customer page lists the exact lines that produced the number), derived and
never stored (change a weight and all of history re-scores on the next page
load), and read off behaviour rather than words. The categories on the customer
list are buckets of that percentage, not a separate taxonomy.

- **Bosh sahifa** (`/admin/`) — a worklist: who to talk to today, ranked by the
  probability, plus the KPI row, the category split, funnel drop-off, message
  volume and paid orders.
- **Mijozlar** (`/admin/users/`) — one row per chat: who they are, their
  probability as a bar, their category, the funnel stage, messages in and out,
  last activity, and a Telegram link. Category chips double as the filter;
  search by name/@username/phone/chat id; export the filtered list as CSV.
- **One customer** (`/admin/users/<tenant>/<chat_id>/`) — their **conversation**,
  rendered like the chat it mirrors, next to a **"Telegramda ochish"** link that
  opens the same chat in Telegram, the score with the reasons that produced it,
  their orders and payments, and their queued messages. Two operator actions live
  here: a **category selector** — `faol / sotib olgan / yopilgan`, the two facts
  a person knows and the system cannot see; either terminal state cancels their
  queued messages, and neither invents an order, so revenue stays what the
  payment providers confirmed and **«1 oydan keyin yozish»** (1 hafta /
  1 oy / 3 oy, with an optional note saying what to raise; the text itself is
  written by the agent at send time, in the client's language).
- **Avto-xabarlar** (`/admin/outreach/`) — the automatic messages in one place:
  the pacing rules (on/off, the hours they may be sent in, a per-chat daily cap,
  a minimum gap), the queue with *hozir* / *+1 kun* / *bekor* on every row, and
  what actually went out, in the words the client read. Saved rules reach the
  running bots within seconds (`panel_settings` → `db/settings.py`), no restart.
- **Bot qoidalari** (`/admin/prompts/`) — the agents' system prompts, edited in the
  browser. The bots re-read the files on every reply, so a save takes effect
  without a restart; each save keeps the previous version under
  `prompts/.history/` and the page offers them back.
- **Holat** (`/admin/health/`) — are the units running, is anyone waiting on an
  unanswered message, how big is the queue, plus a restart button for the bot
  units. A quiet day and a deliberately disabled unit are reported as notes
  rather than alarms.
- **Sozlamalar** (`/admin/settings/`) — the handful of `.env` values an operator
  changes (support phone, operator chat ids, memorial dates, send hour). A fixed
  allow-list: credentials are neither shown nor writable. These are read at
  startup, so the page says to restart and links to the button.
- **Tahlil** (`/admin/analytics/`) — the same over a period (7/14/30/90 days):
  volume, reply ratio, revenue, top senders and top writers, the touch mix, and
  **per-tool failure rates** read off the corpus.

The panel is **light-themed and bilingual** — a `uz`/`en` switch in the topbar,
Uzbek by default. Django's own strings come from the catalogues it ships; the
panel's own copy from a plain table in `crm/i18n.py` (the server has no gettext,
and a build step for two languages is a worse trade than a dictionary).

Categories are derived, not stored (`crm/scoring.py::BUCKETS`): `mijoz` (paid)
and `yopilgan` (lead lost) are decided by fact; the rest — `issiq` (≥65%),
`iliq` (≥40%), `sovuq` (≥15%), `muz` — are slices of the probability. "An
operator is holding this chat" is a separate flag, shown as a badge and offered
as its own filter, because it is orthogonal to how likely they are to buy.
Change the weights, the decay or the bucket floors at the top of that module and
every page re-scores on the next load.

**Eval chats are excluded by default.** `scripts/eval_run.py` runs its cases in
a reserved high chat-id block and is supposed to delete what it made; runs that
died leave rows behind, and one synthetic *paid* order is enough to make the
revenue KPI a lie. Every page says how many chats it hid and links to `?eval=1`
to bring them back — the filter is never silent, because the id window can in
principle catch a real chat.

**The bot does not write first.** That is the default (`db/outreach.py`,
enabled = false), and it is the owner's decision: the funnel's own follow-ups
said nothing a client needed and read like a spam service. Every funnel asks
`db/outreach.py` before a scheduled touch goes out, and with the switch off a
funnel-initiated one is **cancelled** — deferring it would park the row in the
queue for as long as the switch stays off, which is indefinitely.

Three kinds of message still reach a client. **Replies**, always — the agent
answers whenever someone writes. **Transactional** ones, which are not scheduled
tasks at all: a payment confirmation, the finished-work photos. And a message an
**operator scheduled by hand** from the customer page (`operator_followup`,
marked `source: operator` in the task payload), which the switch lets through —
"the bot must not chase people" is a rule about the funnel's initiative, not a
gag on the person running it. Quiet hours apply to those too; the daily cap and
the minimum gap do not.

Turning the switch back on re-enables the drip, paced by the hours, the per-chat
daily cap and the minimum gap. Either way the message reads like a person rather
than a notification: `Agent.compose` is told to send a plain 1-3 sentence chat
message, and `_plain_message` strips whatever costume slips through anyway —
emoji, bold, bullet lists, `Eslatma:` labels.

Three things it deliberately does **not** do:

- **It does not run funnel transitions.** Changing an order's status here updates
  the row; it does not message the client or re-plan their scheduled touches
  (`funnel.on_*` runs in the bot process). Drive the funnel from the operator bot.
- **It does not let you edit the record.** `conversation_events` and
  `maskan_payments` are read-only: one is the corpus the agents are debugged and
  trained from, the other is state the payment providers assert.
- **It does not let you edit a touch's text.** Every automatic message is written
  by the agent at send time, in the client's own language, so there is nothing to
  edit until it exists — what the panel controls is *whether*, *when* and *how
  often*, plus the queue itself.

## Adding a new tenant

1. Create `apps/<tenant>/`
2. Define `config.py` with envs, `tools.py` with `@tool` functions, and a `prompts/` folder
3. Write an entrypoint that builds a `Tenant(id, agents, channels, sync_jobs)` and hands it to `Runtime`
4. Register a console script in `pyproject.toml`

No changes to the shared core packages should be required for typical tenants.
