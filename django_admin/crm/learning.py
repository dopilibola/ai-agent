"""The «O‘qitish» page: teaching the agent from real conversations.

The agent already learns one way — `core/learning/example_store.py` pulls
*approved* question→answer pairs out of `learning_examples` and folds the
closest ones into the prompt on every reply. Until now that table could only be
filled and reviewed from a terminal (`scripts/gold_review.py`). This module is
the browser side of the same loop, and it adds the two sources that never
reached the corpus at all:

* **Uploaded chats** — the sales conversations people had *before* the bot, in a
  Telegram export (`result.json`), a WhatsApp export (`.txt`) or pasted text.
  The operator says which speaker was the seller; every customer message is
  paired with the seller's next reply.
* **Hand-written experience** — "when a customer says X, answer like this".

and two harvests from the bot's own record (operator replies after a handoff,
and the bot's replies in chats that ended in a sale).

Everything passes the same **filter** before a person sees it: too-short or
media-only turns, replies that came hours later, duplicates — and money, phone
and card numbers are masked, because an example may teach *phrasing* only; the
price still comes from the catalogue on every quote. Nothing reaches the bot
until someone approves it (hand-written experience is the exception: a person
writing it here *is* the approval).

Source is encoded in `source_thread_id` (no migration): ``import:<name>``,
``manual``, ``won:<thread_id>``, or a bare thread id for operator replies.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from django.db import connections
from django.utils import timezone

DB = "ai_sales"
SETTING_KEY = "learning"
DEFAULTS = {"enabled": True, "k": 3, "min_score": 0.25}

# Same window `example_store.harvest` uses: a reply hours later is usually not
# an answer to the question above it.
MAX_GAP_HOURS = 6
MIN_REPLY_CHARS = 12
MAX_REPLY_CHARS = 1500
CONTEXT_TURNS = 6

SOURCES = {
    "operator": "Operator javobi",
    "import": "Yuklangan chat",
    "manual": "Qo‘lda yozilgan",
    "won": "Sotuv bo‘lgan suhbat",
}


def source_of(thread_id: str | None) -> str:
    tid = thread_id or ""
    if tid.startswith("import:"):
        return "import"
    if tid.startswith("won:"):
        return "won"
    if tid == "manual" or not tid:
        return "manual"
    return "operator"


def real_thread(thread_id: str | None) -> str:
    """The corpus thread behind a row, or "" when it has none."""
    tid = thread_id or ""
    if tid.startswith("won:"):
        return tid[4:]
    return "" if source_of(tid) in ("import", "manual") else tid


# ------------------------------------------------------------------ masking

MONEY = "{narx}"
PHONE = "{telefon}"
CARD = "{karta}"

_LONG_NUMBER = re.compile(r"\+?\d[\d \-()]{7,}\d")
_MONEY_RE = re.compile(r"\b\d[\d  .,]*\d\b")
_LINK = re.compile(r"https?://\S+|t\.me/\S+")


def mask(text: str) -> str:
    """Cards, phones, then money. Order matters: a phone number is also a run
    of digits that the money rule would otherwise turn into a price."""
    def _long(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 13:
            return CARD
        if len(digits) >= 9:
            return PHONE
        return m.group(0)

    def _money(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 4 and 1900 <= int(digits) <= 2100:
            return m.group(0)  # a year, not a price
        return MONEY if len(digits) >= 4 else m.group(0)

    text = _LONG_NUMBER.sub(_long, text or "")
    text = _LINK.sub("{havola}", text)
    return _MONEY_RE.sub(_money, text).strip()


# ------------------------------------------------------------------ parsing


@dataclass
class Msg:
    speaker: str
    text: str
    at: datetime | None = None


def _tg_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(p if isinstance(p, str) else str(p.get("text", "")) for p in value)
    return ""


def _parse_telegram(data: dict) -> list[Msg]:
    # A full-account export nests chats; take the one with the most messages.
    if "messages" not in data and isinstance(data.get("chats"), dict):
        chats = data["chats"].get("list") or []
        data = max(chats, key=lambda c: len(c.get("messages") or []), default={})
    out: list[Msg] = []
    for m in data.get("messages") or []:
        if m.get("type") != "message":
            continue
        text = _tg_text(m.get("text"))
        if not text.strip():
            if m.get("photo") or m.get("media_type") or m.get("file"):
                text = "[media]"
            else:
                continue
        at = None
        try:
            at = datetime.fromisoformat(m["date"])
        except (KeyError, ValueError, TypeError):
            pass
        out.append(Msg(str(m.get("from") or m.get("from_id") or "?"), text, at))
    return out


# 12/31/23, 10:15 - Name: text   |   [31.12.2023, 10:15:00] Name: text
_WA = re.compile(
    r"^\[?(\d{1,2}[./]\d{1,2}[./]\d{2,4}),?\s+(\d{1,2}:\d{2})(?::\d{2})?\s*(?:[AP]M)?\]?\s*[-–]?\s*([^:]{1,60}):\s?(.*)$"
)
_PLAIN = re.compile(r"^([^:\n]{1,40}):\s?(.+)$")


def _wa_date(d: str, hm: str) -> datetime | None:
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%m/%d/%y", "%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y"):
        try:
            day = datetime.strptime(d, fmt)
            h, mi = hm.split(":")
            return day.replace(hour=int(h), minute=int(mi))
        except ValueError:
            continue
    return None


def _parse_lines(text: str) -> list[Msg]:
    lines = text.splitlines()
    wa_hits = sum(1 for ln in lines[:50] if _WA.match(ln))
    out: list[Msg] = []
    for ln in lines:
        ln = ln.rstrip()
        if not ln:
            continue
        m = _WA.match(ln) if wa_hits else None
        if m:
            d, hm, who, body = m.groups()
            out.append(Msg(who.strip(), body, _wa_date(d, hm)))
            continue
        p = None if wa_hits else _PLAIN.match(ln)
        if p:
            out.append(Msg(p.group(1).strip(), p.group(2)))
        elif out:
            # A continuation line of the previous message.
            out[-1].text += "\n" + ln
    for m in out:
        if m.text.strip() in ("<Media omitted>", "<Без медиафайлов>", "<Медиа отсутствуют>"):
            m.text = "[media]"
    return out


def parse_chat(raw: str) -> list[Msg]:
    raw = (raw or "").lstrip("﻿")
    if raw.lstrip().startswith("{"):
        try:
            return _parse_telegram(json.loads(raw))
        except (ValueError, AttributeError):
            pass
    return _parse_lines(raw)


def speakers(msgs: list[Msg]) -> list[tuple[str, int]]:
    return Counter(m.speaker for m in msgs).most_common(8)


# ------------------------------------------------------------------ pairing + filter


@dataclass
class Candidate:
    question: str
    reply: str
    context: list[dict]
    event_id: int | None = None


@dataclass
class FilterReport:
    kept: list[Candidate] = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)

    def drop(self, reason: str) -> None:
        self.dropped[reason] += 1


def _has_words(text: str) -> bool:
    return bool(re.search(r"[^\W\d_]{2,}", text or ""))


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


_GREETING = re.compile(
    r"^\W*(assalomu?\s*alay[kq]um|salom|hayrli\s+\w+|здравствуйте|привет|добрый\s+\w+|hello|hi)\W*$",
    re.I,
)


def _strip_greeting(text: str) -> str:
    """A customer's "Assalomu alaykum" on its own line is not the question, and
    it drags the trigram score of the real question down for every lookup."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    kept = [ln for ln in lines if not _GREETING.match(ln.strip())]
    return "\n".join(kept or lines)


def filter_pairs(raw_pairs: list[tuple[str, str, list[dict], int | None, float | None]],
                 existing: set[tuple[str, str]]) -> FilterReport:
    """Every source goes through this. `raw_pairs` items are
    (question, reply, context, event_id, gap_hours)."""
    report = FilterReport()
    seen = set(existing)
    for question, reply, context, event_id, gap in raw_pairs:
        q, r = mask(_strip_greeting(question)), mask(reply)
        if not _has_words(q) or q == "[media]":
            report.drop("Savolda matn yo‘q (rasm/stiker)")
            continue
        if not _has_words(r) or len(r) < MIN_REPLY_CHARS:
            report.drop("Javob juda qisqa («ok», «ha»)")
            continue
        if len(r) > MAX_REPLY_CHARS:
            report.drop("Javob juda uzun")
            continue
        if gap is not None and gap > MAX_GAP_HOURS:
            report.drop("Javob soatlab kechikkan")
            continue
        key = (_norm(q), _norm(r))
        if key in seen:
            report.drop("Takror")
            continue
        seen.add(key)
        ctx = [{"role": c["role"], "text": mask(c["text"])[:600]} for c in context]
        report.kept.append(Candidate(q[:1000], r, ctx, event_id))
    return report


def pairs_from_messages(msgs: list[Msg], seller: str):
    """Merge each run of consecutive messages from one side into one turn, then
    pair every customer turn with the seller turn that follows it."""
    turns: list[tuple[str, str, datetime | None, datetime | None]] = []
    for m in msgs:
        role = "operator" if m.speaker == seller else "user"
        if turns and turns[-1][0] == role:
            r, t, first, _ = turns[-1]
            turns[-1] = (r, t + "\n" + m.text, first, m.at)
        else:
            turns.append((role, m.text, m.at, m.at))
    out = []
    for i in range(1, len(turns)):
        prev, cur = turns[i - 1], turns[i]
        if prev[0] == "user" and cur[0] == "operator":
            gap = None
            if prev[3] and cur[2]:
                gap = (cur[2] - prev[3]).total_seconds() / 3600
            ctx = [{"role": r, "text": t} for r, t, _, _ in turns[max(0, i - CONTEXT_TURNS):i]]
            out.append((prev[1], cur[1], ctx, None, gap))
    return out


# ------------------------------------------------------------------ store


def _existing_keys(tenant: str) -> set[tuple[str, str]]:
    from crm.models import LearningExample

    return {
        (_norm(q), _norm(r))
        for q, r in LearningExample.objects.filter(tenant_id=tenant).values_list(
            "question", "human_reply"
        )
    }


def _insert(tenant: str, cands: list[Candidate], *, thread: str, approved=None,
            category: str = "") -> int:
    from crm.models import LearningExample

    now = timezone.now()
    n = 0
    for c in cands:
        if c.event_id is not None and LearningExample.objects.filter(
            source_event_id=c.event_id
        ).exists():
            continue
        LearningExample.objects.create(
            tenant_id=tenant,
            question=c.question,
            context=c.context,
            ai_attempt="",
            human_reply=c.reply,
            category=category or None,
            approved=approved,
            source_thread_id=thread,
            source_event_id=c.event_id,
            created_at=now,
            reviewed_at=now if approved is not None else None,
        )
        n += 1
    return n


def import_chat(tenant: str, raw: str, seller: str, label: str, category: str = ""):
    msgs = parse_chat(raw)
    report = filter_pairs(pairs_from_messages(msgs, seller), _existing_keys(tenant))
    label = re.sub(r"[^\w.\- ]", "", label or "chat")[:80] or "chat"
    added = _insert(tenant, report.kept, thread=f"import:{label}", category=category)
    return added, report


def add_manual(tenant: str, question: str, reply: str, category: str = "") -> tuple[bool, str]:
    report = filter_pairs([(question, reply, [], None, None)], _existing_keys(tenant))
    if not report.kept:
        reason = next(iter(report.dropped), "")
        return False, reason
    _insert(tenant, report.kept, thread="manual", approved=True, category=category)
    return True, ""


def harvest_operator(tenant: str, limit: int = 500) -> tuple[int, FilterReport]:
    """Operator replies after a handoff — the port of `example_store.harvest`,
    run through the shared filter."""
    with connections[DB].cursor() as cur:
        cur.execute(
            """
            select id, thread_id, text, created_at from conversation_events
            where tenant_id = %s and role = 'operator' and coalesce(text, '') <> ''
              and id not in (select coalesce(source_event_id, -1) from learning_examples)
            order by id desc limit %s
            """,
            [tenant, limit],
        )
        rows = cur.fetchall()
        raw = []
        threads = {}
        for event_id, thread_id, reply, created_at in rows:
            cur.execute(
                """
                select role, text, created_at from conversation_events
                where thread_id = %s and id < %s and role in ('user', 'assistant', 'operator')
                order by id desc limit %s
                """,
                [thread_id, event_id, CONTEXT_TURNS],
            )
            tail = list(reversed(cur.fetchall()))
            users = [(t, at) for r, t, at in tail if r == "user" and t]
            if not users:
                continue
            question, asked_at = users[-1]
            gap = (created_at - asked_at).total_seconds() / 3600 if asked_at and created_at else None
            ctx = [{"role": r, "text": t or ""} for r, t, _ in tail]
            raw.append((question, reply, ctx, event_id, gap))
            threads[event_id] = thread_id
    report = filter_pairs(raw, _existing_keys(tenant))
    added = 0
    for c in report.kept:
        added += _insert(tenant, [c], thread=threads[c.event_id])
    return added, report


def harvest_won(tenant: str, limit_chats: int = 200) -> tuple[int, FilterReport]:
    """The bot's own replies in chats that ended in a sale: a paid order, or a
    lead an operator marked as bought. Still pending — a sale at the end does
    not make every reply on the way good."""
    if tenant != "maskan":
        # Only maskan records a sale in its own tables.
        return 0, FilterReport()
    with connections[DB].cursor() as cur:
        cur.execute(
            """
            select distinct chat_id from (
              select chat_id from maskan_orders where status in ('paid','accepted','completed')
              union select chat_id from maskan_leads where status = 'won'
            ) s limit %s
            """,
            [limit_chats],
        )
        chats = [r[0] for r in cur.fetchall()]
        raw = []
        threads = {}
        for chat_id in chats:
            cur.execute(
                """
                select id, thread_id, role, text, created_at from conversation_events
                where tenant_id = %s and chat_id = %s
                  and role in ('user','assistant','operator') and coalesce(text,'') <> ''
                order by id
                """,
                [tenant, chat_id],
            )
            events = cur.fetchall()
            for i, (eid, tid, role, text, at) in enumerate(events):
                if role != "assistant" or i == 0 or events[i - 1][2] != "user":
                    continue
                # Merge the run of customer messages that this reply answers.
                j = i - 1
                parts = []
                while j >= 0 and events[j][2] == "user":
                    parts.insert(0, events[j][3])
                    j -= 1
                ctx = [{"role": e[2], "text": e[3]} for e in events[max(0, i - CONTEXT_TURNS):i]]
                gap = (at - events[i - 1][4]).total_seconds() / 3600
                raw.append(("\n".join(parts), text, ctx, eid, gap))
                threads[eid] = tid
    report = filter_pairs(raw, _existing_keys(tenant))
    added = 0
    for c in report.kept:
        added += _insert(tenant, [c], thread="won:" + threads[c.event_id])
    return added, report


# ------------------------------------------------------------------ settings


def load_settings(tenant: str) -> dict:
    from crm.models import PanelSetting

    rows = PanelSetting.objects.filter(key=SETTING_KEY, tenant_id__in=("", tenant))
    by_tenant = {r.tenant_id: (r.value or {}) for r in rows}
    return {**DEFAULTS, **by_tenant.get("", {}), **by_tenant.get(tenant, {})}


def save_settings(tenant: str, data: dict) -> dict:
    from crm.models import PanelSetting

    try:
        k = max(1, min(8, int(data.get("k", 3))))
    except (TypeError, ValueError):
        k = 3
    try:
        min_score = max(0.05, min(0.9, float(str(data.get("min_score", 0.25)).replace(",", "."))))
    except (TypeError, ValueError):
        min_score = 0.25
    value = {"enabled": bool(data.get("enabled")), "k": k, "min_score": round(min_score, 2)}
    PanelSetting.objects.update_or_create(
        tenant_id=tenant, key=SETTING_KEY,
        defaults={"value": value, "updated_at": timezone.now()},
    )
    return value


# ------------------------------------------------------------------ test


def preview(tenant: str, query: str, settings: dict, limit: int = 8) -> list[dict]:
    """What the bot would pull for this message — the same trigram rule the
    runtime falls back to — plus the near misses, marked, so the operator can
    see where the threshold cuts."""
    query = (query or "").strip()
    if not query:
        return []
    with connections[DB].cursor() as cur:
        cur.execute(
            """
            select id, question, human_reply, similarity(question, %s) as score
            from learning_examples
            where tenant_id = %s and approved is true
            order by score desc limit %s
            """,
            [query, tenant, limit],
        )
        rows = cur.fetchall()
    out = []
    used = 0
    for rid, q, r, score in rows:
        score = float(score or 0)
        hit = settings["enabled"] and score > settings["min_score"] and used < settings["k"]
        used += hit
        out.append({"id": rid, "question": q, "reply": r, "score": round(score, 2), "used": hit})
    return out


def counts(tenant: str) -> dict:
    from crm.models import LearningExample

    qs = LearningExample.objects.filter(tenant_id=tenant)
    return {
        "pending": qs.filter(approved__isnull=True).count(),
        "approved": qs.filter(approved=True).count(),
        "rejected": qs.filter(approved=False).count(),
    }

