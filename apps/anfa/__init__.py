"""Anfa tenant — Anfa clinic (Tashkent) service-catalog advisor.

The clinic keeps its own local-only CRM and registers patients offline; it
exports its service list as Excel, which we ingest. The agent advises clients
on services + prices and directs them to visit the clinic — no online booking.

Channels (named by role, mirroring oygul's customer/merchant):
  - "customer" — Telegram userbot (real account): the client-facing surface
  - "merchant" — Telegram bot (@BotFather): also serves the catalog agent to
                 clients who message the bot directly; receives operator-handoff
                 notifications + the "Подключить ИИ" button
  - KnowledgeBaseSync job — mirrors the Postgres catalog into the vector DB on a
                            short schedule so Excel re-imports become searchable
"""
