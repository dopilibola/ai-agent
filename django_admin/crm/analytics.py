"""Aggregations behind the panel's dashboard, user directory and analytics pages.

Everything here answers one of two questions:

* **Who is this chat?** — one row per ``(tenant_id, chat_id)`` carrying the
  counts that matter (how many messages they sent, how many were sent *to*
  them, what they ordered, when they were last seen), a **buy probability**
  (``crm/scoring.py``) and the category that percentage falls into.
* **How is it going over time?** — daily series and breakdowns for the charts.

The joins are done in Python rather than SQL on purpose. The three shared
tables are keyed by ``(tenant_id, chat_id)`` while the tenant tables are keyed
by ``chat_id`` alone, so there is no single join key; and at this size (low
thousands of events, ~100 chats) grouping in the database and stitching in
Python is both simpler and fast. If a tenant ever grows past ~100k events this
is the module to move into SQL views.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable

from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone

from crm import scoring
from crm.scoring import BUCKETS, BUCKET_BY_KEY, Bucket

# `scripts/eval_run.py` runs its scripted cases in a reserved high chat-id block
# (`EVAL_CHAT_BASE`), and is supposed to delete the leads/orders/graves it made.
# Runs that crashed before cleanup leave rows behind, and a synthetic "paid"
# order counted as revenue is worse than no number at all — so these are hidden
# by default and shown with `?eval=1`.
#
# The window is deliberately narrow. Real Telegram ids passed 1e9 years ago, but
# a small band below it was in use, so this can in principle hide a real chat:
# that is why it is a filter and not a permanent exclusion, and why the pages
# say how many rows it hid.
EVAL_CHAT_MIN = 990_000_000
EVAL_CHAT_MAX = 999_999_999


def is_eval_chat(chat_id: int) -> bool:
    return EVAL_CHAT_MIN <= chat_id <= EVAL_CHAT_MAX


# Roles in `conversation_events`, split by who produced them. `outbound` is the
# funnel's own scheduled touches — the message a lead receives without asking —
# which is why it is counted separately from the agent's replies.
# How much of a conversation the customer page renders inline. Long enough that
# almost every dialogue fits whole; the full transcript is one click away.
CONVERSATION_LIMIT = 300

INBOUND_ROLES = ("user",)
SENT_ROLES = ("assistant", "outbound", "operator")

# The categories on the users page are buckets of the buy probability, so the
# taxonomy lives with the scoring that produces it.
SEGMENTS: tuple[Bucket, ...] = BUCKETS
SEGMENT_BY_KEY = BUCKET_BY_KEY


@dataclass
class ChatRow:
    tenant_id: str
    chat_id: int
    name: str = ""
    username: str = ""
    inbound: int = 0          # messages the customer sent
    replies: int = 0          # agent replies
    touches: int = 0          # scheduled funnel touches
    operator_msgs: int = 0    # messages a human operator sent
    tools: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    # Split by direction, because "did they answer our last message" is one of
    # the strongest buy signals there is and needs both sides dated.
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    muted_at: datetime | None = None
    # Which transport this chat last used. A chat id means a different person on
    # each — a Telegram id and a WhatsApp phone number are different address
    # spaces — so the "open the conversation" link has to know.
    channel: str = ""
    tokens: int = 0
    lead_stage: int | None = None
    lead_status: str = ""
    phone: str = ""
    close_reason: str = ""
    orders: int = 0
    paid_orders: int = 0
    revenue: int = 0          # so‘m, paid orders only
    tasks_pending: int = 0
    next_touch_at: datetime | None = None
    segment: str = "muz"
    percent: int = 0
    reasons: list[tuple[str, int]] = field(default_factory=list)

    @property
    def sent(self) -> int:
        """Everything the system sent *to* this person."""
        return self.replies + self.touches + self.operator_msgs

    @property
    def answered_last(self) -> bool:
        """Did they write after the last thing we sent them?

        False when we never wrote at all: "answered us" is only a signal if
        there was something to answer.
        """
        if self.last_inbound_at is None or self.last_outbound_at is None:
            return False
        return self.last_inbound_at > self.last_outbound_at

    @property
    def thread_id(self) -> str:
        return f"{self.tenant_id}:customer:{self.chat_id}"

    @property
    def label(self) -> str:
        return self.name or (f"@{self.username}" if self.username else str(self.chat_id))

    @property
    def segment_obj(self) -> Bucket:
        return SEGMENT_BY_KEY.get(self.segment, SEGMENTS[-1])

    @property
    def contact_url(self) -> str:
        """A link that opens this exact conversation, on its own transport.

        WhatsApp is the easy case: the chat id *is* the phone number, so
        `wa.me/<number>` opens it anywhere. Telegram prefers `t.me/<username>`,
        which works in every client including the web app; without a handle,
        `tg://user?id=` is the only addressable form — it opens the installed
        app and does nothing in a browser, which is why the username wins when
        there is one.
        """
        if self.channel == "whatsapp":
            return f"https://wa.me/{self.chat_id}"
        if self.username:
            return f"https://t.me/{self.username.lstrip('@')}"
        return f"tg://user?id={self.chat_id}"

    @property
    def contact_label(self) -> str:
        return "WhatsApp" if self.channel == "whatsapp" else "Telegram"

    @property
    def bar_width(self) -> int:
        return max(2, self.percent) if self.percent else 0


def chat_rows(tenant: str = "", include_eval: bool = False) -> list[ChatRow]:
    """One row per chat, with counts, money, a buy probability and its category.

    Six grouped queries plus three small table scans — no per-row queries, so
    the cost does not grow with the number of chats shown.
    """
    from crm.models import (
        ChatProfile,
        ChatTokenUsage,
        ConversationEvent,
        MaskanLead,
        MaskanOrder,
        MaskanScheduledTask,
        MutedChat,
    )

    now = timezone.now()
    rows: dict[tuple[str, int], ChatRow] = {}

    def slot(tenant_id: str, chat_id: int) -> ChatRow:
        key = (tenant_id, chat_id)
        if key not in rows:
            rows[key] = ChatRow(tenant_id=tenant_id, chat_id=chat_id)
        return rows[key]

    events = ConversationEvent.objects.values(
        "tenant_id", "chat_id", "role", "channel"
    ).annotate(n=Count("id"), first=Min("created_at"), last=Max("created_at"))
    if tenant:
        events = events.filter(tenant_id=tenant)
    for e in events:
        row = slot(e["tenant_id"], e["chat_id"])
        role, n, last = e["role"], e["n"], e["last"]
        if role == "user":
            row.inbound += n
            row.last_inbound_at = _later(row.last_inbound_at, last)
        elif role == "assistant":
            row.replies += n
            row.last_outbound_at = _later(row.last_outbound_at, last)
        elif role == "outbound":
            row.touches += n
            row.last_outbound_at = _later(row.last_outbound_at, last)
        elif role == "operator":
            row.operator_msgs += n
            row.last_outbound_at = _later(row.last_outbound_at, last)
        elif role == "tool":
            row.tools += n
        # `outcome` rows are funnel bookkeeping, not messages — counted in
        # neither direction, but they still date the conversation.
        if e["first"] and (row.first_seen is None or e["first"] < row.first_seen):
            row.first_seen = e["first"]
        # The channel of the newest event wins, so a customer who moved from one
        # transport to another is linked on the one they are actually using.
        if e["channel"] and (row.last_seen is None or (last and last >= row.last_seen)):
            row.channel = e["channel"]
        row.last_seen = _later(row.last_seen, last)

    profiles = ChatProfile.objects.all()
    usage = ChatTokenUsage.objects.all()
    muted = MutedChat.objects.all()
    if tenant:
        profiles = profiles.filter(tenant_id=tenant)
        usage = usage.filter(tenant_id=tenant)
        muted = muted.filter(tenant_id=tenant)
    for p in profiles:
        row = slot(p.tenant_id, p.chat_id)
        row.name = p.name or ""
        row.username = p.username or ""
        row.last_seen = _later(row.last_seen, p.updated_at)
    for u in usage:
        slot(u.tenant_id, u.chat_id).tokens = u.spent_total_tokens
    for mc in muted:
        slot(mc.tenant_id, mc.chat_id).muted_at = mc.muted_at

    # Tenant-specific domain data. Only Maskan keeps orders in this database.
    if not tenant or tenant == "maskan":
        # Oldest first, so the newest lead is the one left standing. A chat can
        # hold several: the funnel opens a fresh lead when a returning client
        # writes after a closed deal. Everything else in the panel — the state
        # selector, `set_state`, the runtime's `get_active_lead_by_chat` — takes
        # the newest, and a row that disagreed would show a closed deal for a
        # customer who is mid-conversation. (`Meta.ordering` is `-updated_at`,
        # which without this leaves the *oldest* winning.)
        for lead in MaskanLead.objects.order_by("id"):
            row = slot(lead.tenant_id, lead.chat_id)
            row.lead_stage = lead.current_stage
            row.lead_status = lead.status
            row.phone = lead.phone or ""
            row.close_reason = lead.close_reason or ""
        order_agg = MaskanOrder.objects.values("chat_id").annotate(
            n=Count("id"),
            paid=Count("id", filter=Q(status__in=("paid", "accepted", "completed"))),
            revenue=Sum("total", filter=Q(status__in=("paid", "accepted", "completed"))),
        )
        for o in order_agg:
            row = slot("maskan", o["chat_id"])
            row.orders = o["n"]
            row.paid_orders = o["paid"] or 0
            row.revenue = o["revenue"] or 0
        task_agg = (
            MaskanScheduledTask.objects.filter(status="pending")
            .values("chat_id")
            .annotate(n=Count("id"), soonest=Min("scheduled_for"))
        )
        for t in task_agg:
            row = slot("maskan", t["chat_id"])
            row.tasks_pending = t["n"]
            row.next_touch_at = t["soonest"]

    result = list(rows.values())
    if not include_eval:
        result = [r for r in result if not is_eval_chat(r.chat_id)]
    for row in result:
        score = scoring.score_row(row, now)
        row.percent = score.percent
        row.segment = score.bucket.key
        row.reasons = score.reasons
    return result


def _later(current: datetime | None, candidate: datetime | None) -> datetime | None:
    """The later of two optional timestamps."""
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current


def filter_rows(
    rows: Iterable[ChatRow],
    *,
    segment: str = "",
    query: str = "",
    sort: str = "percent",
) -> list[ChatRow]:
    """Filter and order the directory. Default order is the buy probability —
    the panel's whole premise is that the top of this list is today's work."""
    out = list(rows)
    if segment == "operator":
        # Not a probability bucket: an orthogonal "a human is holding this chat"
        # flag, exposed as a filter because it is the most urgent thing there is.
        out = [r for r in out if r.muted_at]
    elif segment:
        out = [r for r in out if r.segment == segment]
    if query:
        needle = query.casefold()
        out = [
            r
            for r in out
            if needle in str(r.chat_id)
            or needle in r.name.casefold()
            or needle in r.username.casefold()
            or needle in r.phone.casefold()
        ]
    keys = {
        "percent": lambda r: (r.percent, r.last_seen is not None, r.last_seen or ""),
        "last_seen": lambda r: (r.last_seen is not None, r.last_seen or ""),
        "first_seen": lambda r: (r.first_seen is not None, r.first_seen or ""),
        "inbound": lambda r: r.inbound,
        "sent": lambda r: r.sent,
        "touches": lambda r: r.touches,
        "tokens": lambda r: r.tokens,
        "revenue": lambda r: r.revenue,
    }
    out.sort(key=keys.get(sort, keys["percent"]), reverse=True)
    return out


def segment_counts(rows: Iterable[ChatRow]) -> list[tuple[Bucket, int]]:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.segment] = counts.get(r.segment, 0) + 1
    return [(s, counts.get(s.key, 0)) for s in SEGMENTS]


def daily_messages(tenant: str = "", days: int = 30, include_eval: bool = False) -> dict:
    """Messages per day, split into received / replied / scheduled touches."""
    from crm.models import ConversationEvent

    since = timezone.now() - timedelta(days=days - 1)
    qs = ConversationEvent.objects.filter(created_at__gte=since)
    if tenant:
        qs = qs.filter(tenant_id=tenant)
    if not include_eval:
        qs = qs.exclude(chat_id__gte=EVAL_CHAT_MIN, chat_id__lte=EVAL_CHAT_MAX)
    agg = qs.values("role", "created_at__date").annotate(n=Count("id"))

    labels = [
        (timezone.localtime(since).date() + timedelta(days=i)) for i in range(days)
    ]
    series = {"inbound": [0] * days, "replies": [0] * days, "touches": [0] * days}
    index = {d: i for i, d in enumerate(labels)}
    for r in agg:
        d: date = r["created_at__date"]
        i = index.get(d)
        if i is None:
            continue
        role = r["role"]
        if role == "user":
            series["inbound"][i] += r["n"]
        elif role == "assistant":
            series["replies"][i] += r["n"]
        elif role in ("outbound", "operator"):
            series["touches"][i] += r["n"]
    return {"labels": labels, "series": series}


def daily_revenue(days: int = 30, include_eval: bool = False) -> dict:
    """Paid Maskan orders per day, and the money they carried."""
    from crm.models import MaskanOrder

    since = timezone.now() - timedelta(days=days - 1)
    qs = MaskanOrder.objects.filter(
        created_at__gte=since, status__in=("paid", "accepted", "completed")
    )
    if not include_eval:
        qs = qs.exclude(chat_id__gte=EVAL_CHAT_MIN, chat_id__lte=EVAL_CHAT_MAX)
    agg = (
        qs
        .values("created_at__date")
        .annotate(n=Count("id"), total=Sum("total"))
    )
    labels = [
        (timezone.localtime(since).date() + timedelta(days=i)) for i in range(days)
    ]
    index = {d: i for i, d in enumerate(labels)}
    orders = [0] * days
    money = [0] * days
    for r in agg:
        i = index.get(r["created_at__date"])
        if i is None:
            continue
        orders[i] = r["n"]
        money[i] = r["total"] or 0
    return {"labels": labels, "orders": orders, "money": money}


def funnel_stages(tenant: str = "maskan", include_eval: bool = False) -> list[tuple[str, int]]:
    """Active leads per stage — the drop-off, read left to right."""
    from crm.models import MaskanLead

    qs = MaskanLead.objects.filter(status="active")
    if not include_eval:
        qs = qs.exclude(chat_id__gte=EVAL_CHAT_MIN, chat_id__lte=EVAL_CHAT_MAX)
    agg = (
        qs
        .values("current_stage")
        .annotate(n=Count("id"))
        .order_by("current_stage")
    )
    return [(f"{r['current_stage']}-bosqich", r["n"]) for r in agg]


def touch_actions(tenant: str = "maskan", days: int = 30) -> list[tuple[str, int]]:
    """Which scheduled touches actually fired — the "sms" mix, by action type."""
    from crm.models import MaskanScheduledTask

    since = timezone.now() - timedelta(days=days)
    agg = (
        MaskanScheduledTask.objects.filter(status="done", executed_at__gte=since)
        .values("action_type")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    return [(r["action_type"], r["n"]) for r in agg]


def tool_health(tenant: str = "", days: int = 30) -> list[dict]:
    """Per-tool call counts and failure rate, read off the corpus.

    `db/training.py` writes an `ok` flag into each tool row's `meta`, and it
    recognises all three failure shapes the tenants use — so this is the one
    place that answers "which tool is failing in production" without guessing.
    """
    from crm.models import ConversationEvent

    since = timezone.now() - timedelta(days=days)
    qs = ConversationEvent.objects.filter(role="tool", created_at__gte=since)
    if tenant:
        qs = qs.filter(tenant_id=tenant)
    stats: dict[str, dict] = {}
    for meta in qs.values_list("meta", flat=True):
        meta = meta or {}
        name = meta.get("tool") or meta.get("name") or "—"
        row = stats.setdefault(name, {"tool": name, "calls": 0, "failed": 0})
        row["calls"] += 1
        if meta.get("ok") is False:
            row["failed"] += 1
    out = list(stats.values())
    for row in out:
        row["rate"] = (row["failed"] / row["calls"] * 100) if row["calls"] else 0.0
    out.sort(key=lambda r: (-r["failed"], -r["calls"]))
    return out


def tenants() -> list[str]:
    """The tenants that actually have conversations.

    `.order_by()` clears the model's default ordering first, and that is the
    whole point: `ConversationEvent.Meta.ordering` puts `id` into the query, and
    a DISTINCT over `(tenant_id, id)` is distinct per *row* — which is how the
    tenant picker ended up listing "default" a few thousand times.
    """
    from crm.models import ConversationEvent

    return sorted(
        ConversationEvent.objects.order_by()
        .values_list("tenant_id", flat=True)
        .distinct()
    )


def chat_daily(tenant: str, chat_id: int, days: int = 30) -> dict:
    """The same daily split as `daily_messages`, for a single chat."""
    from crm.models import ConversationEvent

    since = timezone.now() - timedelta(days=days - 1)
    agg = (
        ConversationEvent.objects.filter(
            tenant_id=tenant, chat_id=chat_id, created_at__gte=since
        )
        .values("role", "created_at__date")
        .annotate(n=Count("id"))
    )
    labels = [
        (timezone.localtime(since).date() + timedelta(days=i)) for i in range(days)
    ]
    index = {d: i for i, d in enumerate(labels)}
    series = {"inbound": [0] * days, "replies": [0] * days, "touches": [0] * days}
    for r in agg:
        i = index.get(r["created_at__date"])
        if i is None:
            continue
        if r["role"] == "user":
            series["inbound"][i] += r["n"]
        elif r["role"] == "assistant":
            series["replies"][i] += r["n"]
        elif r["role"] in ("outbound", "operator"):
            series["touches"][i] += r["n"]
    return {"labels": labels, "series": series}


def chat_detail(tenant: str, chat_id: int) -> dict:
    """Everything the customer page shows besides the score: the conversation
    itself, the lead, and what was ordered and paid.

    The queued touches are deliberately *not* here — they come from
    `crm/outreach.py`, which is also what renders and acts on them.
    """
    from crm.models import ConversationEvent, MaskanLead, MaskanOrder, MaskanPayment

    lead = MaskanLead.objects.filter(tenant_id=tenant, chat_id=chat_id).first()
    orders = list(MaskanOrder.objects.filter(chat_id=chat_id).order_by("-created_at"))
    payments = list(
        MaskanPayment.objects.filter(chat_id=chat_id).order_by("-created_at")
    )
    # The conversation itself, oldest first — the customer page is meant to be
    # read like the chat it mirrors, not like a log. Tool calls and funnel
    # bookkeeping are dropped: they are visible in the full transcript, and here
    # they would break the reading.
    events = ConversationEvent.objects.filter(
        tenant_id=tenant, chat_id=chat_id
    ).exclude(role__in=("tool", "outcome"))
    conversation = list(reversed(list(events.order_by("-id")[:CONVERSATION_LIMIT])))
    total_events = events.count()
    return {
        "lead": lead,
        "orders": orders,
        "payments": payments,
        "conversation": conversation,
        "conversation_total": total_events,
        "conversation_trimmed": total_events > len(conversation),
    }


def eval_chat_count(tenant: str = "") -> int:
    """How many chats the eval filter is hiding — shown so it is never silent."""
    from crm.models import ConversationEvent

    qs = ConversationEvent.objects.filter(
        chat_id__gte=EVAL_CHAT_MIN, chat_id__lte=EVAL_CHAT_MAX
    )
    if tenant:
        qs = qs.filter(tenant_id=tenant)
    return qs.values("tenant_id", "chat_id").distinct().count()
