"""The panel's own pages: home, the customer directory, one customer, automatic
messages, and the trends.

These live outside the model admin because they answer questions no single
table can. The organising idea is one number: **how close is this person to
buying** (`crm/scoring.py`). The home page ranks by it, the directory's
categories are buckets of it, and a customer's page shows the lines that
produced it — so the panel is a worklist ("talk to these five today"), not a
database browser.

Each function is a plain view; `PanelAdminSite.get_urls` wraps them in
`admin_view` so they require a staff login like every other admin page.
"""

from __future__ import annotations

import csv
import re
from datetime import timedelta

from django.contrib import admin, messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from crm import analytics as an
from crm import env_settings as envs
from crm import health as hp
from crm.i18n import t
from crm import outreach as out
from crm import prompts as pr
from panel import charts

# Colours shared by every messages chart, so "blue = they wrote" holds
# everywhere on the panel.
INBOUND_C = "#2563eb"
REPLY_C = "#12b76a"
TOUCH_C = "#7f56d9"

PERIODS = (7, 14, 30, 90)

# The default tenant for the pages that need one concrete funnel to talk about.
DEFAULT_TENANT = "maskan"

# How far ahead the customer page can schedule a message. A month is the one the
# owner asked for — grave care is seasonal, and a month is when it is worth
# asking again — the others are there so the button is not a straitjacket.
FOLLOWUP_OPTIONS = ((7, "1 hafta"), (30, "1 oy"), (90, "3 oy"))


def _include_eval(request) -> bool:
    """`?eval=1` brings the scripted eval chats back into every number."""
    return request.GET.get("eval") == "1"


def _period(request) -> int:
    try:
        days = int(request.GET.get("days", 30))
    except ValueError:
        return 30
    return days if days in PERIODS else 30


def _tenant(request) -> str:
    return (request.GET.get("tenant") or "").strip()


def _messages_chart(data) -> str:
    return charts.stacked_bars(
        data["labels"],
        [
            ("kelgan", data["series"]["inbound"], INBOUND_C),
            ("javob", data["series"]["replies"], REPLY_C),
            ("tegish", data["series"]["touches"], TOUCH_C),
        ],
    )


def _money(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


# --------------------------------------------------------------- home


def dashboard(request):
    """Today's work, in one screen: who to talk to, and is anything stuck."""
    tenant = _tenant(request)
    days = _period(request)
    include_eval = _include_eval(request)
    rows = an.chat_rows(tenant, include_eval)
    now = timezone.now()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    msgs = an.daily_messages(tenant, days, include_eval)
    rev = an.daily_revenue(days, include_eval)
    queue = out.queue_counts(tenant or DEFAULT_TENANT)

    live = [r for r in rows if r.segment not in ("mijoz", "yopilgan")]
    hot = [r for r in live if r.percent >= 40]
    today = [r for r in rows if r.last_inbound_at and r.last_inbound_at >= day_ago]
    paid = [r for r in rows if r.segment == "mijoz"]

    # Built here rather than in the template because the hints interleave numbers
    # with words; `t()` is per-request, so it sees the language the visitor chose.
    kpis = [
        (t("Sotib olishga yaqin"), str(len(hot)), t("40% dan yuqori mijozlar")),
        (t("Bugun yozganlar"), str(len(today)), t("oxirgi 24 soat")),
        (
            t("Yangi (7 kun)"),
            str(sum(1 for r in rows if r.first_seen and r.first_seen >= week_ago)),
            t("birinchi marta yozganlar"),
        ),
        (
            t("Sotib olgan"),
            str(len(paid)),
            f"{_money(sum(r.revenue for r in rows))} {t('so‘m')}",
        ),
        (
            t("Navbatdagi avto-xabar"),
            str(queue["pending"]),
            f"{queue['sent_week']} {t('tasi shu hafta yuborilgan')}",
        ),
        (
            t("Operator qo‘lida"),
            str(sum(1 for r in rows if r.muted_at)),
            t("AI o‘chirilgan chatlar"),
        ),
    ]

    context = {
        **admin.site.each_context(request),
        "title": t("Bosh sahifa"),
        "kpis": kpis,
        "hot_rows": an.filter_rows(live, sort="percent")[:12],
        "segment_bar": charts.segment_bar(
            an.segment_counts(rows), base=reverse("admin:users")
        ),
        "messages_chart": _messages_chart(msgs),
        "revenue_chart": charts.stacked_bars(
            rev["labels"], [("to‘lov", rev["orders"], REPLY_C)], height=110
        ),
        "revenue_total": sum(rev["money"]),
        "funnel": charts.hbars(an.funnel_stages(tenant or DEFAULT_TENANT, include_eval)),
        "queue": queue,
        "tenant": tenant,
        "tenants": an.tenants(),
        "days": days,
        "periods": PERIODS,
        "include_eval": include_eval,
        "eval_hidden": 0 if include_eval else an.eval_chat_count(tenant),
        "app_list": admin.site.get_app_list(request),
    }
    return TemplateResponse(request, "admin/dashboard.html", context)


# ---------------------------------------------------------------- users


def _user_rows(request):
    tenant = _tenant(request)
    segment = (request.GET.get("segment") or "").strip()
    query = (request.GET.get("q") or "").strip()
    sort = (request.GET.get("sort") or "percent").strip()
    include_eval = _include_eval(request)
    all_rows = an.chat_rows(tenant, include_eval)
    rows = an.filter_rows(all_rows, segment=segment, query=query, sort=sort)
    return (
        all_rows,
        rows,
        {
            "tenant": tenant,
            "segment": segment,
            "q": query,
            "sort": sort,
            "include_eval": include_eval,
            "eval_hidden": 0 if include_eval else an.eval_chat_count(tenant),
        },
    )


def users(request):
    all_rows, rows, params = _user_rows(request)
    page = Paginator(rows, 50).get_page(request.GET.get("page"))

    querystring = request.GET.copy()
    querystring.pop("page", None)

    context = {
        **admin.site.each_context(request),
        "title": t("Mijozlar"),
        "page": page,
        "total": len(rows),
        "grand_total": len(all_rows),
        # The categories are the filter, so the page needs the counts, not a chart.
        "segment_counts": an.segment_counts(all_rows),
        "operator_count": sum(1 for r in all_rows if r.muted_at),
        "tenants": an.tenants(),
        "querystring": querystring.urlencode(),
        "sorts": (
            ("percent", "sotib olish ehtimoli"),
            ("last_seen", "oxirgi faollik"),
            ("inbound", "kelgan xabarlar"),
            ("sent", "yuborilgan xabarlar"),
            ("revenue", "tushum"),
            ("first_seen", "birinchi kelgan"),
            ("tokens", "token"),
        ),  # translated in the template with |t
        **params,
    }
    return TemplateResponse(request, "admin/users.html", context)


def users_csv(request):
    """The same filtered list as the page, for analysis in a spreadsheet."""
    _all_rows, rows, _params = _user_rows(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    response["Content-Disposition"] = f'attachment; filename="mijozlar-{stamp}.csv"'
    # Excel on Windows needs the BOM to read UTF-8 names correctly.
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(
        [
            "tenant", "chat_id", "ism", "username", "telefon",
            "ehtimol_foiz", "kategoriya",
            "kelgan", "yuborilgan", "javob", "rejali_tegish", "operator",
            "buyurtma", "tolangan_buyurtma", "tushum_sum", "token",
            "lid_bosqich", "lid_status", "birinchi", "oxirgi", "aloqa",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.tenant_id, r.chat_id, r.name, r.username, r.phone,
                r.percent, r.segment_obj.label,
                r.inbound, r.sent, r.replies, r.touches, r.operator_msgs,
                r.orders, r.paid_orders, r.revenue, r.tokens,
                r.lead_stage or "", r.lead_status,
                r.first_seen.isoformat() if r.first_seen else "",
                r.last_seen.isoformat() if r.last_seen else "",
                r.contact_url,
            ]
        )
    return response


def user_detail(request, tenant: str, chat_id: int):
    """One customer: the conversation itself, why the score is what it is, and
    a link that opens the same chat in Telegram."""
    # `include_eval=True`: this page is addressed by id, so an eval chat must
    # still open when someone follows a link to it.
    row = next(
        (
            r
            for r in an.chat_rows(tenant, include_eval=True)
            if r.chat_id == chat_id and r.tenant_id == tenant
        ),
        None,
    )
    if row is None:
        raise Http404(f"{tenant}:{chat_id} topilmadi")
    detail = an.chat_detail(tenant, chat_id)
    context = {
        **admin.site.each_context(request),
        "title": row.label,
        "row": row,
        "queued": out.upcoming(tenant, chat_id=chat_id, limit=20),
        "state": out.current_state(tenant, chat_id),
        "window_open": out.freeform_window_open(tenant, chat_id, row.channel),
        "states": out.MANUAL_STATES,
        "followup_options": FOLLOWUP_OPTIONS,
        "back": request.GET.get("back") or reverse("admin:users"),
        **detail,
    }
    return TemplateResponse(request, "admin/user_detail.html", context)


def set_state(request):
    """Operator button: move a customer between «faol», «sotib olgan» and «yopilgan».

    Assertions about the relationship, not about money: they change the category
    and stop every queued message, but they do not invent an order, so the
    revenue figures stay what the payment providers actually confirmed. The heat
    categories are not offered here — those are the score's estimate, and
    hand-setting an estimate only makes it untrue.
    """
    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    back = request.POST.get("next") or reverse("admin:users")
    if request.method != "POST":
        return HttpResponseRedirect(back)
    try:
        chat_id = int(request.POST.get("chat_id") or "")
    except ValueError:
        messages.error(request, t("Noto‘g‘ri chat_id"))
        return HttpResponseRedirect(back)
    state = (request.POST.get("state") or "").strip()
    if not out.set_state(
        tenant, chat_id, state, reason=(request.POST.get("reason") or "").strip()
    ):
        messages.warning(
            request,
            t("Bu chatda murojaat (lid) yo‘q — avval mijoz bilan suhbat boshlanishi kerak."),
        )
    elif state == out.PURCHASED_STATUS:
        messages.success(
            request,
            t(
                "«Sotib olgan» deb belgilandi. Rejadagi avto-xabarlar bekor qilindi. "
                "Tushum hisobiga qo‘shilmaydi — u faqat haqiqiy to‘lovlardan yig‘iladi."
            )
            + " "
            + t(
                "{n} kundan keyin «bu oy ham parvarish qilaymizmi?» degan taklif "
                "avtomatik yuboriladi — kerak bo‘lmasa pastdan bekor qiling."
            ).replace("{n}", str(out.REPEAT_AFTER_DAYS)),
        )
    elif state == out.CLOSED_STATUS:
        messages.success(
            request, t("Yopilgan deb belgilandi, rejadagi xabarlar bekor qilindi.")
        )
    else:
        messages.success(request, t("Faol holatga qaytarildi."))
    return HttpResponseRedirect(back)


def operator_reply(request):
    """Send one message to a customer, as the operator, and step the AI back.

    Queued rather than sent inline: the panel is a separate process and holds no
    channel. The scheduler delivers it on its next tick, which also gives the
    send the funnel's own routing, muting and corpus logging for free.
    """
    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    back = request.POST.get("next") or reverse("admin:users")
    if request.method != "POST":
        return HttpResponseRedirect(back)
    try:
        chat_id = int(request.POST.get("chat_id") or "")
    except ValueError:
        messages.error(request, t("Noto‘g‘ri chat_id"))
        return HttpResponseRedirect(back)
    text = (request.POST.get("text") or "").strip()
    if not text:
        messages.warning(request, t("Xabar matni bo‘sh."))
        return HttpResponseRedirect(back)
    if out.send_operator_message(tenant, chat_id, text) is None:
        messages.error(request, t("Xabarni navbatga qo‘yib bo‘lmadi."))
    else:
        messages.success(
            request,
            t("Xabar navbatga qo‘yildi — bir daqiqa ichida yuboriladi. AI bu chatda jim bo‘ladi."),
        )
    return HttpResponseRedirect(back)


def schedule_followup(request):
    """Operator button: write to this person again in a month (or a week, or three)."""
    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    back = request.POST.get("next") or reverse("admin:users")
    if request.method != "POST":
        return HttpResponseRedirect(back)
    try:
        chat_id = int(request.POST.get("chat_id") or "")
    except ValueError:
        messages.error(request, "Noto‘g‘ri chat_id")
        return HttpResponseRedirect(back)
    try:
        days = int(request.POST.get("days") or 30)
    except ValueError:
        days = 30
    days = days if days in dict(FOLLOWUP_OPTIONS) else 30
    task = out.schedule_followup(
        tenant, chat_id, days=days, note=(request.POST.get("note") or "").strip()
    )
    if task is None:
        messages.error(request, t("Bu bo‘lim uchun rejalashtirish qo‘llab-quvvatlanmaydi."))
    else:
        when = timezone.localtime(task.scheduled_for).strftime("%d.%m.%Y %H:%M")
        messages.success(
            request,
            f"{when} — " + t("shu vaqtda yoziladi, matnini AI mijozning tilida yozadi."),
        )
    return HttpResponseRedirect(back)


def unmute(request):
    """Clear one mute — the panel's equivalent of the "Подключить ИИ" button.

    Scoped to both key columns, which is the reason this is hand-written: the
    same Telegram user can hold a chat with more than one tenant's bot, and only
    the one asked for may be un-muted.
    """
    from crm.models import MutedChat

    back = request.POST.get("next") or reverse("admin:users")
    if request.method != "POST":
        return HttpResponseRedirect(back)
    tenant_id = request.POST.get("tenant_id") or ""
    try:
        chat_id = int(request.POST.get("chat_id") or "")
    except ValueError:
        messages.error(request, "Noto‘g‘ri chat_id")
        return HttpResponseRedirect(back)
    deleted, _ = MutedChat.objects.filter(tenant_id=tenant_id, chat_id=chat_id).delete()
    if deleted:
        messages.success(request, f"{tenant_id}:{chat_id} — " + t("AI qayta yoqildi."))
    else:
        messages.warning(request, f"{tenant_id}:{chat_id} — " + t("mute holatida emas edi."))
    return HttpResponseRedirect(back)


# ------------------------------------------------------- automatic messages


def outreach(request):
    """The automatic messages: the rules, what is queued, and what went out."""
    tenant = _tenant(request) or DEFAULT_TENANT
    context = {
        **admin.site.each_context(request),
        "title": t("Avto-xabarlar"),
        "tenant": tenant,
        "tenants": an.tenants() or [DEFAULT_TENANT],
        "policy": out.load_policy(tenant),
        "counts": out.queue_counts(tenant),
        "queued": out.upcoming(tenant, limit=80),
        "recent": out.recent_sent(tenant, limit=25),
        "hours": range(0, 24),
    }
    return TemplateResponse(request, "admin/outreach.html", context)


def outreach_save(request):
    """Save the pacing rules. The bots re-read them within seconds."""
    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    back = f"{reverse('admin:outreach')}?tenant={tenant}"
    if request.method != "POST":
        return HttpResponseRedirect(back)
    saved = out.save_policy(
        tenant,
        {
            "enabled": request.POST.get("enabled") == "on",
            "quiet_from": request.POST.get("quiet_from"),
            "quiet_to": request.POST.get("quiet_to"),
            "daily_cap": request.POST.get("daily_cap"),
            "min_gap_minutes": request.POST.get("min_gap_minutes"),
        },
    )
    if saved["enabled"]:
        messages.success(
            request,
            "Saqlandi: avto-xabarlar yoqilgan, "
            f"{saved['quiet_from']}:00–{saved['quiet_to']}:00 orasida, "
            f"kuniga {saved['daily_cap']} tagacha.",
        )
    else:
        messages.warning(
            request,
            "Saqlandi: avto-xabarlar o‘chirildi. Navbatdagilar o‘chib ketmaydi — "
            "qayta yoqqaningizda yuboriladi.",
        )
    return HttpResponseRedirect(back)


def outreach_task(request):
    """One queued touch: cancel it, push it back a day, or release it now."""
    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    back = request.POST.get("next") or f"{reverse('admin:outreach')}?tenant={tenant}"
    if request.method != "POST":
        return HttpResponseRedirect(back)
    action = request.POST.get("action") or ""
    try:
        task_id = int(request.POST.get("task_id") or "")
    except ValueError:
        messages.error(request, t("Noto‘g‘ri vazifa raqami."))
        return HttpResponseRedirect(back)

    handlers = {
        "cancel": (
            lambda: out.cancel_task(tenant, task_id),
            t("Xabar bekor qilindi."),
        ),
        "postpone": (
            lambda: out.postpone_task(tenant, task_id, days=1),
            t("Bir kunga surildi."),
        ),
        "now": (
            lambda: out.send_now(tenant, task_id),
            t("Navbatga qo‘yildi — ruxsat etilgan eng yaqin vaqtda ketadi."),
        ),
    }
    handler = handlers.get(action)
    if handler is None:
        messages.error(request, t("Noma’lum amal."))
    elif handler[0]():
        messages.success(request, handler[1])
    else:
        messages.warning(request, t("Bajarilmadi — bu xabar endi navbatda emas."))
    return HttpResponseRedirect(back)


def outreach_stop_chat(request):
    """Stop every queued automatic message for one chat.

    The AI still answers when the client writes — this only stops us writing
    first, which is what an operator taking over a conversation actually wants.
    """
    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    back = request.POST.get("next") or reverse("admin:users")
    if request.method != "POST":
        return HttpResponseRedirect(back)
    try:
        chat_id = int(request.POST.get("chat_id") or "")
    except ValueError:
        messages.error(request, "Noto‘g‘ri chat_id")
        return HttpResponseRedirect(back)
    n = out.cancel_for_chat(tenant, chat_id)
    if n:
        messages.success(request, f"{n} " + t("ta rejadagi xabar bekor qilindi."))
    else:
        messages.info(request, t("Bu chat uchun rejadagi xabar yo‘q edi."))
    return HttpResponseRedirect(back)


# -------------------------------------------------------------- analytics


def analytics(request):
    tenant = _tenant(request)
    days = _period(request)
    include_eval = _include_eval(request)
    rows = an.chat_rows(tenant, include_eval)
    msgs = an.daily_messages(tenant, days, include_eval)
    rev = an.daily_revenue(days, include_eval)

    totals = {
        "inbound": sum(msgs["series"]["inbound"]),
        "replies": sum(msgs["series"]["replies"]),
        "touches": sum(msgs["series"]["touches"]),
    }
    active = [r for r in rows if r.inbound]
    context = {
        **admin.site.each_context(request),
        "title": t("Tahlil"),
        "tenant": tenant,
        "tenants": an.tenants(),
        "days": days,
        "periods": PERIODS,
        "include_eval": include_eval,
        "eval_hidden": 0 if include_eval else an.eval_chat_count(tenant),
        "totals": totals,
        "avg_sent": (sum(r.sent for r in rows) / len(rows) if rows else 0),
        "avg_inbound": (sum(r.inbound for r in active) / len(active) if active else 0),
        "reply_ratio": (
            totals["replies"] / totals["inbound"] if totals["inbound"] else 0
        ),
        "messages_chart": _messages_chart(msgs),
        "revenue_chart": charts.stacked_bars(
            rev["labels"], [("to‘lov", rev["orders"], REPLY_C)], height=120
        ),
        "revenue_total": sum(rev["money"]),
        "orders_total": sum(rev["orders"]),
        "segment_bar": charts.segment_bar(
            an.segment_counts(rows), base=reverse("admin:users")
        ),
        "funnel": charts.hbars(an.funnel_stages(tenant or DEFAULT_TENANT, include_eval)),
        "touch_mix": charts.hbars(
            [
                (out.action_label(name), n)
                for name, n in an.touch_actions(tenant or DEFAULT_TENANT, days)
            ],
            colour=TOUCH_C,
        ),
        "top_revenue": charts.hbars(
            [(r.label, r.revenue) for r in an.filter_rows(rows, sort="revenue")[:12]],
            colour=REPLY_C,
        ),
        "top_inbound": charts.hbars(
            [(r.label, r.inbound) for r in an.filter_rows(rows, sort="inbound")[:12]],
            colour=INBOUND_C,
        ),
        "tools": an.tool_health(tenant, days),
    }
    return TemplateResponse(request, "admin/analytics.html", context)


def thread(request, thread_id: str):
    """One conversation in order — the view you want when a reply looked wrong."""
    from crm.models import ChatProfile, ConversationEvent

    events = list(
        ConversationEvent.objects.filter(thread_id=thread_id).order_by("id")
    )
    profile = None
    if events:
        profile = (
            ChatProfile.objects.filter(
                tenant_id=events[0].tenant_id, chat_id=events[0].chat_id
            )
            .order_by("-updated_at")
            .first()
        )
    return TemplateResponse(
        request,
        "admin/transcript.html",
        {
            **admin.site.each_context(request),
            "title": f"Suhbat: {thread_id}",
            "thread_id": thread_id,
            "events": events,
            "profile": profile,
            "changelist_url": (
                reverse("admin:crm_conversationevent_changelist")
                + f"?thread_id={thread_id}"
            ),
        },
    )


# ------------------------------------------------------ the agent's own rules


def prompts(request):
    """Edit what the agent is told to do.

    The bots re-read these files on every reply, so a save here changes the next
    answer without a restart. Previous versions are kept, because a prompt edit
    is the fastest way to break a working sales agent and the file on disk is
    otherwise the only copy.
    """
    tenant = (request.GET.get("tenant") or DEFAULT_TENANT).strip()
    items = pr.list_prompts(tenant)
    key = (request.GET.get("key") or (items[0].key if items else "")).strip()
    info = pr.get_prompt(tenant, key)
    context = {
        **admin.site.each_context(request),
        "title": t("Bot qoidalari"),
        "tenant": tenant,
        "tenants": pr.tenants_with_prompts(),
        "items": items,
        "info": info,
        "content": pr.read_prompt(info) if info else "",
        "missing": bool(info and not info.exists),
        "history": pr.history(info) if info else [],
        "restore": (request.GET.get("restore") or "").strip(),
        "restore_content": (
            pr.read_history(info, request.GET.get("restore") or "") if info else ""
        ),
    }
    return TemplateResponse(request, "admin/prompts.html", context)


def prompts_save(request):
    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    key = (request.POST.get("key") or "").strip()
    back = f"{reverse('admin:prompts')}?tenant={tenant}&key={key}"
    if request.method != "POST":
        return HttpResponseRedirect(back)
    # The path is resolved from the registry, never from the request — the
    # browser only ever names a (tenant, key) pair.
    info = pr.get_prompt(tenant, key)
    if info is None:
        messages.error(request, t("Bunday qoida fayli yo‘q."))
        return HttpResponseRedirect(reverse("admin:prompts"))
    ok, note = pr.write_prompt(info, request.POST.get("content", ""))
    (messages.success if ok else messages.error)(request, note)
    return HttpResponseRedirect(back)


# ------------------------------------------------------------------- health


def health(request):
    context = {
        **admin.site.each_context(request),
        "title": t("Tizim holati"),
        **hp.summary(),
    }
    return TemplateResponse(request, "admin/health.html", context)


def health_restart(request):
    back = request.POST.get("next") or reverse("admin:health")
    if request.method != "POST":
        return HttpResponseRedirect(back)
    ok, note = hp.restart_unit((request.POST.get("unit") or "").strip())
    (messages.success if ok else messages.error)(request, note)
    return HttpResponseRedirect(back)


# ----------------------------------------------------------------- settings


def settings_env(request):
    context = {
        **admin.site.each_context(request),
        "title": t("Sozlamalar"),
        "fields": envs.FIELDS,
        "values": envs.read_values(),
        "env_path": str(envs.ENV_PATH),
    }
    return TemplateResponse(request, "admin/settings_env.html", context)


def settings_env_save(request):
    back = reverse("admin:settings_env")
    if request.method != "POST":
        return HttpResponseRedirect(back)
    ok, notes = envs.save_values(request.POST)
    for note in notes:
        (messages.success if ok else messages.error)(request, note)
    return HttpResponseRedirect(back)


# ------------------------------------------------------------------ learning
#
# «O‘qitish»: real conversations in, a filtered review queue, and the approved
# pairs the bot folds into its prompt. The logic lives in `crm/learning.py`;
# these views only move form fields to it.

LEARNING_STATUSES = (
    ("pending", "Ko‘rilmagan"),
    ("approved", "Tasdiqlangan"),
    ("rejected", "Rad etilgan"),
    ("all", "Hammasi"),
)
IMPORT_MAX_BYTES = 25 * 1024 * 1024


def _import_dir():
    from django.conf import settings as dj

    path = dj.BASE_DIR / ".imports"
    path.mkdir(exist_ok=True)
    return path


def _learning_back(request, tenant: str) -> HttpResponseRedirect:
    back = request.POST.get("back") or ""
    if back.startswith(reverse("admin:learning")):
        return HttpResponseRedirect(back)
    return HttpResponseRedirect(f"{reverse('admin:learning')}?tenant={tenant}")


def _report_note(request, added: int, report) -> None:
    dropped = ", ".join(f"{t(k)}: {v}" for k, v in report.dropped.most_common())
    msg = f"{t('Qo‘shildi')}: {added}"
    if dropped:
        msg += f" · {t('Filtrdan o‘tmadi')} — {dropped}"
    messages.success(request, msg)


def learning(request):
    from crm import learning as ln
    from crm.models import LearningExample

    tenant = (request.GET.get("tenant") or DEFAULT_TENANT).strip()
    status = request.GET.get("status") or "pending"
    source = request.GET.get("source") or ""
    q = (request.GET.get("q") or "").strip()

    from django.db.models import Q

    imported = Q(source_thread_id__startswith="import:")
    won = Q(source_thread_id__startswith="won:")
    manual = Q(source_thread_id="manual") | Q(source_thread_id__isnull=True)
    qs = LearningExample.objects.filter(tenant_id=tenant).order_by("-id")
    qs = {
        "pending": qs.filter(approved__isnull=True),
        "approved": qs.filter(approved=True),
        "rejected": qs.filter(approved=False),
    }.get(status, qs)
    if source == "import":
        qs = qs.filter(imported)
    elif source == "won":
        qs = qs.filter(won)
    elif source == "manual":
        qs = qs.filter(manual)
    elif source == "operator":
        qs = qs.exclude(imported | won | manual)
    if q:
        qs = qs.filter(Q(question__icontains=q) | Q(human_reply__icontains=q)
                       | Q(category__icontains=q))

    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    rows = [
        {
            "obj": ex,
            "source": ln.source_of(ex.source_thread_id),
            "thread": ln.real_thread(ex.source_thread_id),
            "label": (ex.source_thread_id or "")[7:] if (ex.source_thread_id or "").startswith("import:") else "",
        }
        for ex in page.object_list
    ]

    settings_ = ln.load_settings(tenant)
    test = (request.GET.get("test") or "").strip()
    preview = []
    if test:
        try:
            preview = ln.preview(tenant, test, settings_)
        except Exception as exc:  # pg_trgm missing, DB hiccup
            messages.error(request, f"{t('Sinovda xato')}: {exc}")

    pending_import = None
    token = (request.GET.get("import") or "").strip()
    if token and re.fullmatch(r"[0-9a-f]{32}", token):
        path = _import_dir() / f"{token}.txt"
        if path.exists():
            msgs = ln.parse_chat(path.read_text(encoding="utf-8", errors="replace"))
            pending_import = {
                "token": token,
                "label": request.GET.get("label") or "chat",
                "category": request.GET.get("category") or "",
                "count": len(msgs),
                "speakers": ln.speakers(msgs),
                "sample": msgs[:6],
            }

    from urllib.parse import urlencode

    keep = {k: v for k, v in (("tenant", tenant), ("status", status), ("source", source), ("q", q)) if v}
    context = {
        **admin.site.each_context(request),
        "title": t("O‘qitish"),
        "tenant": tenant,
        "tenants": an.tenants() or [DEFAULT_TENANT],
        "status": status,
        "statuses": LEARNING_STATUSES,
        "source": source,
        "sources": list(ln.SOURCES.items()),
        "q": q,
        "page": page,
        "rows": rows,
        "counts": ln.counts(tenant),
        "settings": settings_,
        "test": test,
        "preview": preview,
        "pending_import": pending_import,
        "querystring": urlencode(keep),
        "back": request.get_full_path(),
    }
    return TemplateResponse(request, "admin/learning.html", context)


def learning_import(request):
    from crm import learning as ln

    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    if request.method != "POST":
        return _learning_back(request, tenant)
    label = (request.POST.get("label") or "").strip()
    category = (request.POST.get("category") or "").strip()[:64]
    token = (request.POST.get("token") or "").strip()
    seller = request.POST.get("seller") or ""

    # Step 2: the operator picked which speaker was the seller.
    if token and seller and re.fullmatch(r"[0-9a-f]{32}", token):
        path = _import_dir() / f"{token}.txt"
        if not path.exists():
            messages.error(request, t("Yuklangan fayl topilmadi — qaytadan yuklang."))
            return _learning_back(request, tenant)
        raw = path.read_text(encoding="utf-8", errors="replace")
        added, report = ln.import_chat(tenant, raw, seller, label, category)
        path.unlink(missing_ok=True)
        _report_note(request, added, report)
        return HttpResponseRedirect(
            f"{reverse('admin:learning')}?tenant={tenant}&status=pending&source=import"
        )

    # Step 1: take the file (or pasted text), keep it on disk, ask who is who.
    upload = request.FILES.get("file")
    if upload:
        if upload.size > IMPORT_MAX_BYTES:
            messages.error(request, t("Fayl juda katta (25 MB gacha)."))
            return _learning_back(request, tenant)
        raw = upload.read().decode("utf-8", errors="replace")
        label = label or upload.name.rsplit(".", 1)[0]
    else:
        raw = request.POST.get("text") or ""
    if not raw.strip():
        messages.error(request, t("Fayl tanlang yoki chat matnini joylang."))
        return _learning_back(request, tenant)
    msgs = ln.parse_chat(raw)
    if len(ln.speakers(msgs)) < 2:
        messages.error(
            request,
            t("Chatdan ikki tomon topilmadi. Telegram (result.json), WhatsApp (.txt) yoki «Ism: matn» ko‘rinishida bo‘lsin."),
        )
        return _learning_back(request, tenant)
    import uuid
    from urllib.parse import urlencode

    token = uuid.uuid4().hex
    (_import_dir() / f"{token}.txt").write_text(raw, encoding="utf-8")
    query = urlencode({"tenant": tenant, "import": token, "label": label or "chat",
                       "category": category})
    return HttpResponseRedirect(f"{reverse('admin:learning')}?{query}#import")


def learning_manual(request):
    from crm import learning as ln

    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    if request.method == "POST":
        ok, reason = ln.add_manual(
            tenant,
            request.POST.get("question") or "",
            request.POST.get("reply") or "",
            (request.POST.get("category") or "").strip()[:64],
        )
        if ok:
            messages.success(request, t("Tajriba saqlandi — bot endi shundan foydalanadi."))
        else:
            messages.error(request, f"{t('Saqlanmadi')}: {t(reason)}")
    return _learning_back(request, tenant)


def learning_harvest(request):
    from crm import learning as ln

    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    if request.method == "POST":
        kind = request.POST.get("kind")
        if kind == "won":
            added, report = ln.harvest_won(tenant)
        else:
            added, report = ln.harvest_operator(tenant)
        _report_note(request, added, report)
    return _learning_back(request, tenant)


def learning_review(request):
    from crm.models import LearningExample

    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    if request.method != "POST":
        return _learning_back(request, tenant)
    action = request.POST.get("action") or ""
    single = request.POST.get("id")
    ids = [single] if single else request.POST.getlist("ids")
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        messages.error(request, t("Hech narsa tanlanmagan."))
        return _learning_back(request, tenant)
    qs = LearningExample.objects.filter(tenant_id=tenant, id__in=ids)
    now = timezone.now()
    if action == "approve":
        n = qs.update(approved=True, reviewed_at=now)
    elif action == "reject":
        n = qs.update(approved=False, reviewed_at=now)
    elif action == "pending":
        n = qs.update(approved=None, reviewed_at=None)
    elif action == "delete":
        n = qs.count()
        qs.delete()
    elif action == "edit" and single:
        question = (request.POST.get("question") or "").strip()
        reply = (request.POST.get("reply") or "").strip()
        if not question or not reply:
            messages.error(request, t("Savol ham, javob ham bo‘sh bo‘lmasin."))
            return _learning_back(request, tenant)
        from crm.learning import mask

        n = qs.update(
            question=mask(question),
            human_reply=mask(reply),
            category=(request.POST.get("category") or "").strip()[:64] or None,
            approved=True,
            reviewed_at=now,
        )
    else:
        return _learning_back(request, tenant)
    messages.success(request, f"{t('Bajarildi')}: {n}")
    return _learning_back(request, tenant)


def learning_settings(request):
    from crm import learning as ln

    tenant = (request.POST.get("tenant") or DEFAULT_TENANT).strip()
    if request.method == "POST":
        ln.save_settings(tenant, request.POST)
        messages.success(request, t("Saqlandi. Bir necha soniyada botga yetadi."))
    return _learning_back(request, tenant)
