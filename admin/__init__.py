"""Internal admin panel API.

A standalone FastAPI service that reads/writes the *shared* Postgres the tenant
bots use — muted chats, per-chat token usage, and LangGraph conversation
checkpoints. It imports none of the Telegram/agent runtime; it's just another
client on the same database, run as its own process (console script
``admin-api`` / pm2 app ``admin-api``).

Requires ``DATABASE_URL`` (Postgres). The JSON/in-memory persistence path is
per-process and not shared across processes, so it has nothing for a separate
panel process to read.
"""
