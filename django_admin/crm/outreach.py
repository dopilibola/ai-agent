"""The panel's side of automatic messaging: the queue, the pacing rules, and
plain-Uzbek names for everything in them.

The bots decide *what* the funnel says; this module is how a person sees and
steers it:

* the pacing rules (`db/outreach.py` reads them back on the bot side),
* the touches that are queued but not yet sent, with the ability to cancel one,
  push it back a day, or release it now,
* what actually went out recently, in the client's own words.

The action names are the point of the labels table. `s4_reminder2` means
nothing to the person running the business; "to‘lov eslatmasi (2-chi)" does, and
a queue nobody can read is a queue nobody will fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

# Mirrors `db/outreach.py::DEFAULTS`. Duplicated rather than imported to keep
# the panel free of the bot runtime (same convention as the mirrored models) —
# when you change one, change the other.
DEFAULTS: dict = {
    # Off: the bot never writes first. Only an operator's own scheduled message
    # goes out. Mirrors `db/outreach.py::DEFAULTS`.
    "enabled": False,
    "quiet_from": 9,
    "quiet_to": 21,
    "daily_cap": 2,
    "min_gap_minutes": 180,
}
SETTING_KEY = "outreach"

# Marker a panel-scheduled task carries in its payload. Mirrors
# `db/outreach.py::OPERATOR_SOURCE` — it is what gets such a message past the
# "the bot never writes first" switch.
OPERATOR_SOURCE = "operator"


# What each scheduled action actually does, in the operator's language. Anything
# missing falls back to its raw key, so a new action type shows up as itself
# rather than disappearing.
ACTION_LABELS: dict[str, str] = {
    # operator-scheduled, every tenant
    "operator_followup": "Operator rejalashtirgan xabar",
    # maskan
    "chat_followup1": "Jim qolganda birinchi eslatma",
    "chat_followup2": "Jim qolganda oxirgi eslatma",
    "s1_followup": "Tanishuv: qaysi qabr degan savol",
    "s1_last": "Tanishuv: oxirgi xabar",
    "s2_offer": "Xizmatlarni taklif qilish",
    "s2_followup": "Xizmat tanlash bo‘yicha eslatma",
    "s2_last": "Xizmat bo‘yicha oxirgi xabar",
    "s3_reminder1": "Narx aytilgan — eslatma (1)",
    "s3_reminder2": "Narx aytilgan — eslatma (2)",
    "s3_last": "Narx bo‘yicha oxirgi xabar",
    "s4_reminder1": "To‘lov eslatmasi (1)",
    "s4_reminder2": "To‘lov eslatmasi (2)",
    "s4_reminder3": "To‘lov eslatmasi (3)",
    "s4_operator_check": "Operatorga: to‘lov kelmadi",
    "s4_expire": "To‘lov muddati tugadi",
    "s5_sla": "Operatorga: buyurtma qabul qilinmadi",
    "s6_sla": "Operatorga: rasmlar kelmadi",
    "s7_review": "Ishdan keyin fikr so‘rash",
    "s7_referral": "Tanishlarga tavsiya so‘rash",
    "s8_recurring": "Takroriy parvarish taklifi",
    "s8_annual": "Yillik parvarish taklifi",
    "memorial": "Ziyorat kunlari oldidan eslatma",
    # byd
    "s1_call_reminder": "Qo‘ng‘iroq eslatmasi",
    "s1_escalation": "Operatorga: javob yo‘q",
    "s2_touch1": "Qiziqtirish (1)",
    "s2_touch2": "Qiziqtirish (2)",
    "s2_touch3": "Qiziqtirish (3)",
    "s2_touch4": "Qiziqtirish (4)",
    "s2_touch5": "Qiziqtirish (5)",
    "s2_reactivation": "Qayta jonlantirish",
    "s4_confirm": "Tasdiqlash",
    "s4_reminder": "Tasdiqlash eslatmasi",
    "s4_operator_confirm": "Operatorga: tasdiqlash",
    "s5_payment_link": "To‘lov havolasi",
    "s5_reminder": "To‘lov eslatmasi",
    "s5_operator_check": "Operatorga: to‘lov tekshiruvi",
    "s7_operator_prepare": "Operatorga: tayyorgarlik",
    "s7_reminder": "Kelish eslatmasi",
    "s7_morning": "Ertalabki eslatma",
    "s8_review": "Fikr so‘rash",
    "s8_referral": "Tavsiya so‘rash",
    "s8_reactivation": "Qayta jonlantirish",
    "s8_birthday": "Tug‘ilgan kun tabrigi",
    "bitrix_sync": "Bitrix bilan sinxron",
    "bitrix_comment": "Bitrixga izoh",
    "bitrix_task": "Bitrixda vazifa",
    "bitrix_task_complete": "Bitrix vazifasi yopildi",
}

# Touches addressed to staff rather than to the client. Mirrors
# `apps/maskan/funnel.py::OPERATOR_ONLY_ACTIONS` — the pacing rules never delay
# these, and the queue marks them so nobody reads an SLA alert as spam.
OPERATOR_ACTIONS = frozenset(
    k
    for k in ACTION_LABELS
    # `operator_followup` is the opposite case — scheduled *by* an operator and
    # addressed to the client — so it is excluded by name.
    if k != "operator_followup"
    and (k.startswith("bitrix_") or "operator" in k or k.endswith("_sla"))
)


def action_label(action_type: str) -> str:
    return ACTION_LABELS.get(action_type, action_type)


def is_operator_action(action_type: str) -> bool:
    return action_type in OPERATOR_ACTIONS


def task_model(tenant: str):
    """The scheduled-task table for a tenant, or None if it has no funnel."""
    from crm.models import BydScheduledTask, MaskanScheduledTask

    return {"maskan": MaskanScheduledTask, "byd": BydScheduledTask}.get(tenant or "maskan")


# ------------------------------------------------------------------ settings


def load_policy(tenant: str) -> dict:
    """The tenant's pacing rules, filled in from the defaults."""
    from crm.models import PanelSetting

    rows = PanelSetting.objects.filter(key=SETTING_KEY, tenant_id__in=("", tenant))
    stored: dict = {}
    by_tenant = {r.tenant_id: (r.value or {}) for r in rows}
    stored.update(by_tenant.get("", {}))
    stored.update(by_tenant.get(tenant, {}))
    return {**DEFAULTS, **stored}


def save_policy(tenant: str, data: dict) -> dict:
    """Validate and store the pacing rules. Returns what was actually saved."""
    from crm.models import PanelSetting

    def clamp(key: str, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(data.get(key, DEFAULTS[key]))))
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    value = {
        "enabled": bool(data.get("enabled")),
        "quiet_from": clamp("quiet_from", 0, 23),
        "quiet_to": clamp("quiet_to", 1, 24),
        "daily_cap": clamp("daily_cap", 1, 20),
        "min_gap_minutes": clamp("min_gap_minutes", 0, 24 * 60),
    }
    PanelSetting.objects.update_or_create(
        tenant_id=tenant,
        key=SETTING_KEY,
        defaults={"value": value, "updated_at": timezone.now()},
    )
    return value


# --------------------------------------------------------------- the queue


@dataclass
class QueuedTouch:
    task_id: int
    tenant_id: str
    chat_id: int
    who: str
    action_type: str
    scheduled_for: object
    stage: int
    attempts: int
    last_error: str
    waiting: bool  # deferred by the pacing gate rather than simply not due yet
    by_operator: bool = False  # a person queued it, so the master switch spares it

    @property
    def label(self) -> str:
        return action_label(self.action_type)

    @property
    def to_operator(self) -> bool:
        return is_operator_action(self.action_type)


def upcoming(tenant: str, *, limit: int = 100, chat_id: int | None = None) -> list[QueuedTouch]:
    """Queued touches, soonest first, labelled with who they are addressed to."""
    from crm.models import ChatProfile

    model = task_model(tenant)
    if model is None:
        return []
    qs = model.objects.filter(status="pending").order_by("scheduled_for")
    if chat_id is not None:
        qs = qs.filter(chat_id=chat_id)
    tasks = list(qs[:limit])
    names = {
        p.chat_id: (p.name or (f"@{p.username}" if p.username else ""))
        for p in ChatProfile.objects.filter(
            tenant_id=tenant, chat_id__in=[t.chat_id for t in tasks]
        )
    }
    return [
        QueuedTouch(
            task_id=t.id,
            tenant_id=tenant,
            chat_id=t.chat_id,
            who=names.get(t.chat_id) or str(t.chat_id),
            action_type=t.action_type,
            scheduled_for=t.scheduled_for,
            stage=t.stage,
            attempts=t.attempts,
            last_error=(t.last_error or ""),
            waiting=bool(t.last_error and t.last_error.startswith("kutilmoqda")),
            by_operator=bool((t.payload or {}).get("source") == OPERATOR_SOURCE),
        )
        for t in tasks
    ]


def recent_sent(tenant: str, *, limit: int = 30, chat_id: int | None = None):
    """What automatic messages actually went out, in the words the client read."""
    from crm.models import ConversationEvent

    qs = ConversationEvent.objects.filter(tenant_id=tenant, role="outbound")
    if chat_id is not None:
        qs = qs.filter(chat_id=chat_id)
    return list(qs.order_by("-id")[:limit])


def queue_counts(tenant: str) -> dict:
    """Headline numbers for the page: queued, waiting, failed, sent this week."""
    from crm.models import ConversationEvent

    model = task_model(tenant)
    if model is None:
        return {"pending": 0, "waiting": 0, "failed": 0, "sent_week": 0, "next_at": None}
    pending = model.objects.filter(status="pending")
    week_ago = timezone.now() - timedelta(days=7)
    return {
        "pending": pending.count(),
        "waiting": pending.filter(last_error__startswith="kutilmoqda").count(),
        "failed": model.objects.filter(status="failed").count(),
        "sent_week": ConversationEvent.objects.filter(
            tenant_id=tenant, role="outbound", created_at__gte=week_ago
        ).count(),
        "next_at": (
            pending.order_by("scheduled_for")
            .values_list("scheduled_for", flat=True)
            .first()
        ),
    }


# --------------------------------------------------------------- the actions
#
# Three verbs, and no fourth. "Cancel" and "postpone" are what an operator
# actually wants; "send now" exists because the most common complaint about a
# queue is that something correct is sitting in it. Editing a touch's *text*
# is deliberately not here — the text is composed by the agent at send time in
# the client's own language, so there is nothing to edit until it exists.


def cancel_task(tenant: str, task_id: int) -> bool:
    model = task_model(tenant)
    if model is None:
        return False
    return bool(
        model.objects.filter(id=task_id, status="pending").update(status="cancelled")
    )


def postpone_task(tenant: str, task_id: int, *, days: int = 1) -> bool:
    model = task_model(tenant)
    if model is None:
        return False
    task = model.objects.filter(id=task_id, status="pending").first()
    if task is None:
        return False
    base = max(task.scheduled_for, timezone.now())
    model.objects.filter(id=task_id).update(
        scheduled_for=base + timedelta(days=days), last_error=None
    )
    return True


def send_now(tenant: str, task_id: int) -> bool:
    """Move a queued touch to «now» so the next scheduler tick fires it.

    The pacing gate still applies — releasing a touch is not a licence to write
    to a client at 03:00 — so this means "as soon as it is allowed", which is
    what an operator means by it anyway.
    """
    model = task_model(tenant)
    if model is None:
        return False
    return bool(
        model.objects.filter(id=task_id, status="pending").update(
            scheduled_for=timezone.now(), last_error=None
        )
    )


def cancel_for_chat(tenant: str, chat_id: int) -> int:
    """Stop every queued automatic message for one chat.

    Does not mark the client do-not-contact: the AI still answers when they
    write. This is "stop writing to them first", which is the thing an operator
    holding a conversation actually needs.
    """
    model = task_model(tenant)
    if model is None:
        return 0
    return int(
        model.objects.filter(chat_id=chat_id, status="pending").update(status="cancelled")
    )


# ------------------------------------------------- operator-driven actions
#
# The two things an operator does to a customer row that the funnel cannot do
# for them: say "this one bought" (a sale the system never saw — cash, a
# transfer, a deal closed on the phone) and "write to them again in a month".

PURCHASED_STATUS = "won"
ACTIVE_STATUS = "active"
CLOSED_STATUS = "closed"   # apps/maskan/models.py::STATUS_CLOSED
PURCHASED_NOTE = "Operator «sotib olgan» deb belgiladi"
CLOSED_NOTE = "Operator yopdi"


# The three states an operator may assert. The heat categories — «Juda yaqin»,
# «Yaqin», «O‘ylayapti» — are deliberately *not* here: those are an estimate the
# score computes, and hand-setting an estimate would only make it untrue. What a
# person knows and the system cannot see is the two terminal facts: they bought,
# or the deal is dead.
MANUAL_STATES: tuple[tuple[str, str, str], ...] = (
    (ACTIVE_STATUS, "Faol", "Ehtimol o‘zi hisoblanadi"),
    (PURCHASED_STATUS, "Sotib olgan", "To‘lagan yoki kelishilgan"),
    (CLOSED_STATUS, "Yopilgan", "Rad etgan yoki keraksiz"),
)
MANUAL_STATE_KEYS = {key for key, _l, _h in MANUAL_STATES}


def current_state(tenant: str, chat_id: int) -> str:
    from crm.models import MaskanLead

    status = (
        MaskanLead.objects.filter(tenant_id=tenant, chat_id=chat_id)
        .order_by("-id")
        .values_list("status", flat=True)
        .first()
    )
    return status if status in MANUAL_STATE_KEYS else ACTIVE_STATUS


def set_state(tenant: str, chat_id: int, state: str, *, reason: str = "") -> bool:
    """Move a chat between «faol», «sotib olgan» and «yopilgan».

    Writes the *lead*, never an order or a payment: an operator asserting a sale
    is a fact about the relationship, and inventing a paid order would put money
    the system never saw into the revenue figures. The score reads the status —
    100% for a sale, 0% for a closed deal — while the revenue column stays what
    the payment providers actually confirmed.

    Either terminal state also clears the chat's queued messages: chasing
    somebody who already bought, or who already said no, is the most annoying
    thing a funnel does.
    """
    from crm.models import MaskanLead

    if state not in MANUAL_STATE_KEYS:
        return False
    lead = (
        MaskanLead.objects.filter(tenant_id=tenant, chat_id=chat_id)
        .order_by("-id")
        .first()
    )
    if lead is None:
        return False
    if state == PURCHASED_STATUS:
        # Only the status. The stage is the funnel's own record of how far the
        # conversation actually got, and moving it would make this change
        # irreversible: reverting to «faol» could not know which stage to go
        # back to, and the score would keep reading the lead as paid.
        lead.status = PURCHASED_STATUS
        lead.close_reason = PURCHASED_NOTE
        cancel_for_chat(tenant, chat_id)
    elif state == CLOSED_STATUS:
        lead.status = CLOSED_STATUS
        lead.close_reason = (reason or CLOSED_NOTE)[:255]
        cancel_for_chat(tenant, chat_id)
    else:
        lead.status = ACTIVE_STATUS
        if lead.close_reason in (PURCHASED_NOTE, CLOSED_NOTE):
            lead.close_reason = ""
    lead.updated_at = timezone.now()
    lead.save(update_fields=["status", "close_reason", "updated_at"])
    return True


def schedule_followup(
    tenant: str, chat_id: int, *, days: int = 30, note: str = ""
) -> object | None:
    """Queue one message to this chat `days` from now, on the operator's behalf.

    `source: operator` in the payload is what gets it past the master switch:
    the bot not writing first is a rule about the *funnel's* initiative, not a
    gag on the person running it. Quiet hours still apply — nobody asked for a
    message at 03:00.

    The text is not stored, because there is none yet: `note` says what to
    raise and the agent writes it at send time in the client's own language.
    """
    from crm.models import MaskanLead

    model = task_model(tenant)
    if model is None:
        return None
    when = timezone.now() + timedelta(days=max(1, int(days)))
    lead = (
        MaskanLead.objects.filter(tenant_id=tenant, chat_id=chat_id)
        .order_by("-id")
        .first()
    )
    return model.objects.create(
        tenant_id=tenant,
        # `lead_id=0` is the funnel's "chat-addressed task" convention: the
        # executor resolves the chat's state when it fires, not now.
        lead_id=(lead.id if lead else 0),
        chat_id=chat_id,
        action_type="operator_followup",
        stage=0,
        payload={"source": OPERATOR_SOURCE, "note": note[:500]},
        scheduled_for=when,
        status="pending",
        attempts=0,
        # Unique per scheduling act: two "write in a month" clicks are two
        # messages, unlike the funnel's plans which reset a single row.
        dedup_key=f"op:{chat_id}:{int(when.timestamp())}",
        created_at=timezone.now(),
    )
