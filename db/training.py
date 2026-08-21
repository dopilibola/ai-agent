"""Conversation event log — the dataset the agents are improved from.

Every customer turn is appended to `conversation_events`: what the customer
wrote, which tools the agent called with what arguments, whether those calls
succeeded, what the agent replied, how many tokens it burned and how long it
took — plus the out-of-band funnel touches and, later, the funnel *outcome*
(stage transitions), which is the label that makes the corpus trainable.

Two properties matter more than completeness:

* **It never breaks a conversation.** Every public function swallows its own
  exceptions; the worst a logging failure can do is lose a row.
* **It is append-only.** The LangGraph checkpointer holds the agent's working
  state (summarised, compacted, overwritten); this holds what actually
  happened, so an export months later still sees the real dialogue.

Heavy deps stay lazily imported inside the functions, like the rest of `db/`.
Set `TRAINING_LOG=0` to turn collection off.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Per-field caps: enough to train on, small enough that one runaway tool result
# can't bloat the table.
MAX_TEXT = 20_000
MAX_TOOL = 4_000

# Uzbek-Cyrillic letters that Russian does not have — the cheapest reliable way
# to tell «Қабристон» from «Кладбище» without a language-detection dependency.
_UZ_CYRILLIC = set("ўқғҳЎҚҒҲ")


def enabled() -> bool:
    """Collection is on whenever Postgres is configured, unless TRAINING_LOG=0."""
    flag = os.getenv("TRAINING_LOG", "1").strip().lower()
    if flag in ("0", "false", "off", "no"):
        return False
    from db.engine import database_configured

    return database_configured()


def new_turn_id() -> str:
    return uuid.uuid4().hex[:32]


def detect_lang(text: Optional[str]) -> Optional[str]:
    """uz_cyrl | uz_latn | ru | other — a cheap script/language heuristic.

    Good enough for slicing the corpus ("how often do clients write Latin Uzbek
    and does the agent answer in Cyrillic?"), not a language detector.
    """
    if not text:
        return None
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    lat = sum(1 for ch in text if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    if cyr == 0 and lat == 0:
        return "other"
    if cyr >= lat:
        return "uz_cyrl" if _UZ_CYRILLIC & set(text) else "ru"
    return "uz_latn"


def _clip(value: Any, limit: int = MAX_TEXT) -> Optional[str]:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + "…[clipped]"


def _json_clip(value: Any, limit: int = MAX_TOOL) -> Optional[str]:
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return _clip(text, limit)


async def log_rows(rows: Iterable[dict]) -> None:
    """Insert prepared event rows. Best-effort: never raises."""
    batch = [r for r in rows if r]
    if not batch or not enabled():
        return
    try:
        from db.engine import get_sessionmaker
        from db.models import ConversationEvent

        now = datetime.now(timezone.utc)
        for row in batch:
            row.setdefault("created_at", now)
            row.setdefault("meta", {})
        async with get_sessionmaker()() as session:
            session.add_all([ConversationEvent(**row) for row in batch])
            await session.commit()
    except Exception:  # pragma: no cover - logging must never break a reply
        logger.debug("training log write failed", exc_info=True)


async def log_event(
    *,
    tenant_id: Optional[str],
    chat_id: Optional[int],
    thread_id: str,
    role: str,
    text: Optional[str] = None,
    channel: Optional[str] = None,
    agent: Optional[str] = None,
    turn_id: Optional[str] = None,
    meta: Optional[dict] = None,
) -> None:
    """Append one event (used for out-of-band sends and outcome labels)."""
    await log_rows(
        [
            {
                "tenant_id": tenant_id or "default",
                "chat_id": int(chat_id or 0),
                "thread_id": thread_id or "",
                "channel": channel,
                "agent": agent,
                "turn_id": turn_id,
                "role": role,
                "text": _clip(text),
                "lang": detect_lang(text),
                "meta": meta or {},
            }
        ]
    )


async def log_turn(
    *,
    tenant_id: Optional[str],
    chat_id: Optional[int],
    thread_id: str,
    channel: Optional[str],
    agent: Optional[str],
    user_text: Optional[str],
    reply_text: Optional[str],
    tool_events: Optional[list[dict]] = None,
    tokens: Optional[dict] = None,
    latency_ms: Optional[int] = None,
    images: int = 0,
    model: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> None:
    """Append one full customer turn: message → tool calls → reply.

    `tool_events` is the list built by `core.agent`: {name, args, result, ok}.
    All rows share a `turn_id` so the export can regroup them.
    """
    if not enabled():
        return
    turn_id = new_turn_id()
    base = {
        "tenant_id": tenant_id or "default",
        "chat_id": int(chat_id or 0),
        "thread_id": thread_id or "",
        "channel": channel,
        "agent": agent,
        "turn_id": turn_id,
    }
    rows: list[dict] = [
        {
            **base,
            "role": "user",
            "text": _clip(user_text),
            "lang": detect_lang(user_text),
            "meta": {"images": int(images)} if images else {},
        }
    ]
    for call in tool_events or []:
        rows.append(
            {
                **base,
                "role": "tool",
                "text": _clip(call.get("result"), MAX_TOOL),
                "lang": None,
                "meta": {
                    "tool": call.get("name"),
                    "args": _json_clip(call.get("args")),
                    "ok": bool(call.get("ok", True)),
                },
            }
        )
    meta: dict = {}
    if tokens:
        meta["tokens"] = tokens
    if latency_ms is not None:
        meta["latency_ms"] = int(latency_ms)
    if model:
        meta["model"] = model
    if prompt_version:
        # Which prompt produced this reply. Without it a prompt edit is
        # unattributable: you can see quality move and never know what moved it.
        meta["prompt_version"] = prompt_version
    if tool_events:
        meta["tools_used"] = [c.get("name") for c in tool_events]
    rows.append(
        {
            **base,
            "role": "assistant",
            "text": _clip(reply_text),
            "lang": detect_lang(reply_text),
            "meta": meta,
        }
    )
    await log_rows(rows)
