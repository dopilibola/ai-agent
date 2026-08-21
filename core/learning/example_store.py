"""Approved operator answers, and getting the right ones back.

The loop this closes: a customer asks something the agent fumbles → a human
takes over and answers → that answer is harvested into `learning_examples` →
someone approves it → the next customer asking something similar gets the
approved wording folded into the agent's prompt. No fine-tune, no redeploy; the
agent's behaviour changes because a person pressed approve.

Two retrieval paths, chosen automatically:

* **embeddings** when `EMBED_MODEL` is configured — pgvector cosine distance;
* **trigram** otherwise (`pg_trgm`), which costs nothing and handles the typo-
  heavy way customers actually write.

Two rules the callers rely on:

* **Only `approved = true` rows are ever retrieved.** An unreviewed operator
  message is raw material, not guidance.
* **Money is masked.** Prices change; an example that hard-codes "280 000 so'm"
  would teach the agent yesterday's price. Amounts become `{narx}` on the way
  in, so an example can only ever teach *phrasing*, and the live figure still
  comes from the catalogue.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

MASK = "{narx}"
_MONEY = re.compile(r"\b\d[\d  .]{3,}\b")
_EMBED_DIMS = 768


@dataclass
class Example:
    question: str
    human_reply: str
    ai_attempt: str = ""
    category: str = ""
    score: float = 0.0


def mask_money(text: str) -> str:
    """Replace figures that look like money with a placeholder.

    Deliberately blunt: a false positive costs an example one masked number, a
    false negative teaches a stale price into every future answer.
    """
    def _sub(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return MASK if len(digits) >= 4 else match.group(0)

    return _MONEY.sub(_sub, text or "")


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL yo'q")
    return dsn.replace("postgresql+psycopg", "postgresql")


async def embed(text: str) -> Optional[list[float]]:
    """Embed one string, or None when no provider is configured.

    Never raises: retrieval must degrade to trigram rather than break a reply.
    """
    model = os.environ.get("EMBED_MODEL", "")
    if not model:
        return None
    try:
        import litellm

        response = await litellm.aembedding(model=model, input=[text])
        vector = response["data"][0]["embedding"]
        if len(vector) != _EMBED_DIMS:
            logger.debug("embedding dims %s != %s — vektor ishlatilmaydi",
                         len(vector), _EMBED_DIMS)
            return None
        return vector
    except Exception:
        logger.debug("embedding failed", exc_info=True)
        return None


async def harvest(tenant_id: str, *, limit: int = 500) -> int:
    """Pull operator replies out of the corpus into pending example rows.

    Idempotent through the unique constraint on `source_event_id`, so it can run
    on a timer.
    """
    import psycopg

    inserted = 0
    with psycopg.connect(_dsn()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            select id, thread_id, text, created_at from conversation_events
            where tenant_id = %s and role = 'operator' and coalesce(text, '') <> ''
              and id not in (select coalesce(source_event_id, -1) from learning_examples)
            order by id desc limit %s
            """,
            (tenant_id, limit),
        )
        for event_id, thread_id, reply, created_at in cur.fetchall():
            cur.execute(
                """
                select role, text from conversation_events
                where thread_id = %s and id < %s and role in ('user', 'assistant')
                order by id desc limit 6
                """,
                (thread_id, event_id),
            )
            tail = list(reversed(cur.fetchall()))
            question = next((t for r, t in reversed(tail) if r == "user"), "")
            ai_attempt = next((t for r, t in reversed(tail) if r == "assistant"), "")
            if not question:
                continue
            import json

            cur.execute(
                """
                insert into learning_examples
                  (tenant_id, question, context, ai_attempt, human_reply,
                   source_thread_id, source_event_id, created_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (source_event_id) do nothing
                """,
                (
                    tenant_id,
                    mask_money(question),
                    json.dumps([{"role": r, "text": mask_money(t or "")} for r, t in tail],
                               ensure_ascii=False),
                    mask_money(ai_attempt or ""),
                    mask_money(reply),
                    thread_id,
                    event_id,
                    created_at,
                ),
            )
            inserted += cur.rowcount
        conn.commit()
    return inserted


async def search(tenant_id: str, query: str, *, k: int = 3) -> list[Example]:
    """The k closest approved examples. Returns [] on any failure."""
    import psycopg

    query = (query or "").strip()
    if not query:
        return []
    try:
        vector = await embed(query)
        with psycopg.connect(_dsn(), connect_timeout=5) as conn:
            cur = conn.cursor()
            if vector is not None:
                cur.execute(
                    """
                    select question, human_reply, ai_attempt, coalesce(category, ''),
                           1 - (embedding <=> %s::vector) as score
                    from learning_examples
                    where tenant_id = %s and approved is true and embedding is not null
                    order by embedding <=> %s::vector limit %s
                    """,
                    (vector, tenant_id, vector, k),
                )
            else:
                cur.execute(
                    """
                    select question, human_reply, ai_attempt, coalesce(category, ''),
                           similarity(question, %s) as score
                    from learning_examples
                    where tenant_id = %s and approved is true
                      and similarity(question, %s) > 0.25
                    order by score desc limit %s
                    """,
                    (query, tenant_id, query, k),
                )
            return [Example(q, h, a, c, float(s or 0)) for q, h, a, c, s in cur.fetchall()]
    except Exception:
        logger.debug("example search failed", exc_info=True)
        return []


def render_block(examples: list[Example]) -> str:
    """Format examples for the prompt. Empty string when there are none, so the
    caller can append unconditionally."""
    if not examples:
        return ""
    lines = [
        "",
        "## Shunga o'xshash holatda xodim qanday javob bergan",
        "",
        f"Quyida operator yozgan, tasdiqlangan javoblar. **Uslub va yondashuvni** shundan ol — "
        f"lekin narx va ma'lumotni doim tool'dan ol ({MASK} — o'rniga jonli narxni qo'y).",
        "",
    ]
    for ex in examples:
        lines.append(f"- Mijoz: «{ex.question.strip()[:200]}»")
        lines.append(f"  Xodim: «{ex.human_reply.strip()[:400]}»")
    return "\n".join(lines)
