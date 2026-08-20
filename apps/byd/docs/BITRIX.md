# BYD ↔ Bitrix24 integration

Full two-way sync between the Telegram funnel (`apps/byd`) and a Bitrix24 CRM
pipeline, per `BYD_Med_ТЗ_Воронка_Финал.docx`. The Telegram operator-bot buttons
keep working — Bitrix is a second, equal control surface.

## What syncs

**Push (Telegram → Bitrix, durable via the scheduled-task queue):**

- Stage-1 capture creates a **contact** (name, phone, Telegram IM link) and a
  **deal** in the BYD pipeline (title, amount, program/arrival/DOB/voucher/chat
  custom fields, `ORIGIN_ID=byd_lead_<id>` for idempotency).
- Every funnel transition moves the deal's stage; a closed lead lands on the
  failure stage (an operator-chosen failure stage in Bitrix is preserved).
- The whole dialogue is mirrored into the deal **timeline**: client messages,
  the AI's replies, every automated touch (drip, reminders, voucher), plus event
  comments (payment received, escalation, do-not-contact, cold archive).
  Disable dialogue mirroring with `BYD_BITRIX_MIRROR_MESSAGES=0` (event
  comments still post).
- Operator to-dos (call-now, check-payment, prepare, confirm) become Bitrix
  **tasks** bound to the deal (`UF_CRM_TASK=D_<dealId>`), completed there when
  closed here.

Pushes ride `byd_scheduled_tasks` (`bitrix_*` action types) — they retry with
backoff if the portal is down and are exempt from funnel cancellations.

**Pull (Bitrix → Telegram, `BitrixFunnelSync` polls every
`BYD_BITRIX_SYNC_INTERVAL_SECONDS`, default 30 s):**

| Operator action in Bitrix | Effect |
| --- | --- |
| Drag to «Не дозвон» | starts the 5-touch drip (same as «📵 Не дозвонился») |
| Drag to «Переговоры» | negotiation handoff (same as «📞 Дозвонился») |
| Drag to «Консультация назначена» | confirmation to the client + arrival reminders — **requires «Программа (7/14/21)» and «Дата заезда» filled in the deal card**; otherwise the operator gets a Telegram nag and the move applies once filled |
| Drag to «Запрос предоплаты» | payment link + 3 daily reminders + check-payment task |
| Drag to «Бронь» | mark paid: voucher PDF, team alert, AI mute, arrival chain |
| Drag to «Подтверждение брони» | arrival-confirmation chain (runs mark-paid first if skipped) |
| Drag to «Успешно реализовано» | post-sale chain (review, referral, +60 d, birthday) |
| Drag to any failure stage | lead closed with that stage's name as the reason; every scheduled touch cancelled |
| Edit Программа / Дата заезда / Дата рождения in the card | pulled onto the lead; amounts recomputed while unpaid, reminders re-anchored, birthday armed |

Backward stage moves are recorded but not applied (money/vouchers can't be
unsent). Deals created by hand in Bitrix are ignored — they have no Telegram
chat. Echo suppression via `byd_leads.bitrix_stage_id` (the stage as last
synced in either direction).

## Setup (one time)

1. **Create an incoming webhook** in the portal: *Developer resources → Other →
   Inbound webhook*. Scopes: **CRM (`crm`)** and **Tasks (`task`)**. The
   webhook's user must have CRM-admin rights (stage/pipeline creation) — timeline
   comments and tasks are authored as this user, so a dedicated «Нигина (бот)»
   employee account makes the CRM history read nicely.
2. **Provision the portal** (idempotent — pipeline, 8 stages, ТЗ failure
   stages, deal fields):

   ```bash
   uv run python scripts/byd_bitrix_setup.py --webhook https://xxx.bitrix24.ru/rest/1/<token>/
   ```

3. **Set the printed env vars** in `.env`:

   ```dotenv
   BYD_BITRIX_WEBHOOK_URL=https://xxx.bitrix24.ru/rest/1/<token>/
   BYD_BITRIX_CATEGORY_ID=<printed id>
   BYD_BITRIX_ASSIGNED_BY_ID=<user id that owns deals/contacts>
   BYD_BITRIX_TASK_RESPONSIBLE_ID=<operator user id for tasks>
   # optional:
   # BYD_BITRIX_CURRENCY=UZS
   # BYD_BITRIX_SYNC_INTERVAL_SECONDS=30
   # BYD_BITRIX_MIRROR_MESSAGES=1
   # BYD_BITRIX_STAGE_MAP={"1": "C5:NEW", ...}   # only for hand-built funnels
   ```

4. **Apply the migration and restart**:

   ```bash
   uv run alembic upgrade head      # adds bitrix_* ref columns (0008)
   pm2 restart byd-all
   ```

5. **Optionally push the pre-existing open leads** into the pipeline:

   ```bash
   uv run python scripts/byd_bitrix_setup.py --backfill
   ```

Unset `BYD_BITRIX_WEBHOOK_URL`/`BYD_BITRIX_CATEGORY_ID` = integration fully
off (no queue rows, no polling).

## Implementation map

- `apps/byd/bitrix.py` — webhook REST client (retry/backoff on
  `QUERY_LIMIT_EXCEEDED`/429), stage mapping, contact/deal full-state sync,
  timeline comments, task mirror.
- `apps/byd/bitrix_sync.py` — the pull `SyncJob` (runs with the userbot +
  scheduler in `byd-all`).
- `apps/byd/funnel.py` — `bitrix_mark_dirty` / `bitrix_comment` enqueue hooks in
  every transition; `bitrix_*` executors in `ACTIONS`; pull-side helpers
  (`apply_remote_details`, `advance_to_confirmation`, `close_from_bitrix`).
- `scripts/byd_bitrix_setup.py` — portal provisioning + `--backfill`.
- Migration `0008_byd_bitrix` — `bitrix_contact_id`/`bitrix_deal_id`/
  `bitrix_stage_id` on `byd_leads`, `bitrix_task_id` on `byd_operator_tasks`.
- `channels/telegram/_telethon_base.py` — generic `outbound_observer` hook
  (BYD uses it to mirror the AI's replies into the timeline).
