"""The super admin section (`/admin/super/…`).

Modelled on what a platform owner needs from a multi-company CRM's creator
panel, translated to what this system actually has:

* **Bosh sahifa** — every tenant at a glance, and what needs attention
  (a unit down, a backup gone stale, a bot switched off, a customer waiting).
* **Bo‘limlar** — one tenant: its numbers, its units, its admins, and the
  switch that stops its bot answering (`panel_settings` key "bot", read live by
  `core/channel.py::_bot_enabled`).
* **Adminlar** — create admins and super admins, assign tenants, block, reset a
  password, close sessions.
* **Jurnal** — the action log (`accounts.AuditEvent`), filterable, CSV.
* **Xavfsizlik** — the policies in force and the numbers that matter.
* **Zaxira nusxalar** — the nightly dumps on disk.

The role gate is `accounts.middleware.RoleMiddleware`; every view here also
checks `is_super` itself, because a page this powerful should not depend on a
single line of configuration.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

from django.contrib import admin, messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.audit import LOGIN, LOGIN_FAILED
from accounts.models import AdminProfile, AuditEvent
from crm.i18n import t

TENANT_NAMES = {
    "maskan": "Maskan",
}
BACKUP_MAX_AGE_HOURS = 30


def _guard(request):
    if not roles.is_super(request.user):
        raise PermissionDenied


def _ctx(request, title: str, **extra) -> dict:
    return {**admin.site.each_context(request), "title": t(title), "super_tab": title, **extra}


def known_tenants() -> list[str]:
    from crm import analytics as an

    found = set()
    try:
        found |= {x for x in an.tenants() if x and not x.endswith("-eval") and x != "default"}
    except Exception:
        pass
    found.add("maskan")
    for p in AdminProfile.objects.all():
        found |= set(p.tenant_list())
    return sorted(found)


# ------------------------------------------------------------------ helpers


def bot_enabled(tenant: str) -> bool:
    from crm.models import PanelSetting

    row = PanelSetting.objects.filter(tenant_id=tenant, key="bot").first()
    return bool((row.value or {}).get("enabled", True)) if row else True


def _active_sessions() -> dict[int, int]:
    """user id → number of live sessions."""
    out: dict[int, int] = {}
    for s in Session.objects.filter(expire_date__gt=timezone.now()):
        try:
            uid = int(s.get_decoded().get("_auth_user_id"))
        except (TypeError, ValueError):
            continue
        out[uid] = out.get(uid, 0) + 1
    return out


def _close_sessions(user_ids: set[int], keep_key: str = "") -> int:
    n = 0
    for s in Session.objects.filter(expire_date__gt=timezone.now()):
        try:
            uid = int(s.get_decoded().get("_auth_user_id"))
        except (TypeError, ValueError):
            continue
        if uid in user_ids and s.session_key != keep_key:
            s.delete()
            n += 1
    return n


def tenant_stats(tenant: str) -> dict:
    from crm.analytics import EVAL_CHAT_MAX, EVAL_CHAT_MIN
    from crm.models import ConversationEvent, LearningExample, MaskanLead, MaskanOrder

    now = timezone.now()
    month = now - timedelta(days=30)
    week = now - timedelta(days=7)
    ev = ConversationEvent.objects.filter(tenant_id=tenant).exclude(
        chat_id__gte=EVAL_CHAT_MIN, chat_id__lte=EVAL_CHAT_MAX
    )
    data = {
        "tenant": tenant,
        "name": TENANT_NAMES.get(tenant, tenant),
        "bot_enabled": bot_enabled(tenant),
        "chats": ev.order_by().values("chat_id").distinct().count(),
        "active_week": ev.filter(role="user", created_at__gte=week)
        .order_by().values("chat_id").distinct().count(),
        "inbound_month": ev.filter(role="user", created_at__gte=month).count(),
        "replies_month": ev.filter(role="assistant", created_at__gte=month).count(),
        "last_inbound": ev.filter(role="user").order_by("-id")
        .values_list("created_at", flat=True).first(),
        "examples": LearningExample.objects.filter(tenant_id=tenant, approved=True).count(),
        "examples_pending": LearningExample.objects.filter(
            tenant_id=tenant, approved__isnull=True
        ).count(),
        "revenue_month": 0,
        "paid_month": 0,
        "open_leads": 0,
        "admins": [
            p.user for p in AdminProfile.objects.select_related("user")
            if tenant in p.tenant_list() and p.user.is_active
        ],
    }
    if tenant == "maskan":
        paid = MaskanOrder.objects.filter(
            status__in=("paid", "accepted", "completed"), paid_at__gte=month
        ).exclude(chat_id__gte=EVAL_CHAT_MIN, chat_id__lte=EVAL_CHAT_MAX)
        agg = paid.aggregate(n=Count("id"), s=Sum("total"))
        data["paid_month"] = agg["n"] or 0
        data["revenue_month"] = agg["s"] or 0
        data["open_leads"] = MaskanLead.objects.filter(status="active").count()
    return data


def backups() -> dict:
    from django.conf import settings as dj

    folder = Path(dj.BACKUP_DIR)
    files = []
    if folder.is_dir():
        for f in folder.glob("*.sql.gz"):
            st = f.stat()
            files.append({
                "name": f.name,
                "kind": "Korpus" if f.name.startswith("corpus") else "To‘liq",
                "size": st.st_size,
                "at": datetime.fromtimestamp(st.st_mtime, tz=timezone.get_current_timezone()),
                "ok": st.st_size > 0,
            })
    files.sort(key=lambda x: x["at"], reverse=True)
    full = [f for f in files if f["kind"] == "To‘liq"]
    last = full[0]["at"] if full else None
    age_h = (timezone.now() - last).total_seconds() / 3600 if last else None
    return {
        "folder": str(folder),
        "exists": folder.is_dir(),
        "files": files,
        "last": last,
        "age_h": age_h,
        "fresh": age_h is not None and age_h <= BACKUP_MAX_AGE_HOURS,
        "total": sum(f["size"] for f in files),
    }


# -------------------------------------------------------------------- pages


def home(request):
    _guard(request)
    from crm import health as hp

    tenants = [tenant_stats(x) for x in known_tenants()]
    health = hp.summary()
    bk = backups()
    attention: list[tuple[str, str]] = []
    for p in health["problems"]:
        attention.append(("bad", p))
    for x in tenants:
        if not x["bot_enabled"]:
            attention.append(("warn", f"{x['name']}: {t('bot o‘chirib qo‘yilgan — mijozlarga javob bermayapti.')}"))
        if x["examples_pending"]:
            attention.append(("note", f"{x['name']}: {x['examples_pending']} {t('ta o‘qitish misoli tasdiq kutmoqda.')}"))
    if not bk["exists"]:
        attention.append(("warn", t("Zaxira nusxalar papkasi topilmadi.")))
    elif not bk["fresh"]:
        attention.append(("bad", t("Oxirgi zaxira nusxa 30 soatdan eski.")))
    day = timezone.now() - timedelta(days=1)
    failed = AuditEvent.objects.filter(action=LOGIN_FAILED, at__gte=day).count()
    if failed >= 5:
        attention.append(("warn", f"{failed} {t('ta muvaffaqiyatsiz kirish urinishi (24 soat).')}"))

    User = get_user_model()
    kpis = [
        (t("Bo‘limlar"), str(len(tenants)),
         f"{sum(1 for x in tenants if x['bot_enabled'])} {t('tasida bot yoqiq')}"),
        (t("Tushum · 30 kun"), f"{sum(x['revenue_month'] for x in tenants):,}".replace(",", " ") + " " + t("so‘m"),
         f"{sum(x['paid_month'] for x in tenants)} {t('ta to‘langan buyurtma')}"),
        (t("Faol mijozlar · 7 kun"), str(sum(x["active_week"] for x in tenants)), t("yozgan mijozlar")),
        (t("Adminlar"), str(User.objects.filter(is_staff=True, is_active=True).count()),
         f"{User.objects.filter(is_superuser=True, is_active=True).count()} {t('tasi super admin')}"),
    ]
    return TemplateResponse(request, "admin/super/home.html", _ctx(
        request, "Super admin", tenants=tenants, kpis=kpis, attention=attention,
        units=health["units"], backup=bk,
        recent=AuditEvent.objects.select_related("user")[:8],
    ))


def tenant_detail(request, tenant: str):
    _guard(request)
    from crm import health as hp

    if tenant not in known_tenants():
        raise Http404
    units = [u for u in hp.unit_statuses() if tenant in u.name]
    return TemplateResponse(request, "admin/super/tenant.html", _ctx(
        request, "Bo‘limlar", stats=tenant_stats(tenant), units=units,
        recent=AuditEvent.objects.filter(tenant=tenant).select_related("user")[:15],
    ))


def tenant_bot(request):
    _guard(request)
    from crm.models import PanelSetting

    tenant = (request.POST.get("tenant") or "").strip()
    if request.method == "POST" and tenant in known_tenants():
        enabled = request.POST.get("enabled") == "1"
        PanelSetting.objects.update_or_create(
            tenant_id=tenant, key="bot",
            defaults={"value": {"enabled": enabled}, "updated_at": timezone.now()},
        )
        messages.success(request, t("Bot yoqildi.") if enabled else
                         t("Bot o‘chirildi — bir necha soniyada mijozlarga javob berishni to‘xtatadi."))
    return HttpResponseRedirect(reverse("admin:super_tenant", args=[tenant or "maskan"]))


def admins(request):
    _guard(request)
    User = get_user_model()
    sessions = _active_sessions()
    rows = []
    for u in User.objects.filter(is_staff=True).order_by("-is_superuser", "username"):
        profile = AdminProfile.objects.filter(user=u).first()
        rows.append({
            "user": u,
            "role": roles.role_of(u),
            "tenants": profile.tenant_list() if profile else [],
            "sections": list(roles.sections_of(u)) if u.is_superuser else (profile.section_list() if profile else []),
            "readonly": bool(profile and profile.readonly) and not u.is_superuser,
            "phone": profile.phone if profile else "",
            "note": profile.note if profile else "",
            "sessions": sessions.get(u.id, 0),
        })
    return TemplateResponse(request, "admin/super/admins.html", _ctx(
        request, "Adminlar", rows=rows, all_tenants=known_tenants(),
        edit=request.GET.get("edit") or "",
        sections=roles.SECTIONS, default_sections=roles.DEFAULT_SECTIONS,
        section_labels=roles.SECTION_LABELS,
        presets={k: ",".join(v) for k, v in roles.PRESETS.items()},
    ))


def _apply_role(user, role: str, tenants: list[str], post=None):
    user.is_staff = True
    user.is_superuser = role == roles.SUPER
    user.save()
    profile, _ = AdminProfile.objects.get_or_create(user=user)
    profile.tenants = ",".join(tenants)
    if post is not None:
        # A super admin holds every section; the boxes only mean something for an admin.
        profile.sections = ",".join(roles.clean_sections(post.getlist("sections")))
        profile.readonly = post.get("readonly") == "on" and role == roles.ADMIN
    return profile


def _sections_missing(role: str, post) -> bool:
    return role == roles.ADMIN and not roles.clean_sections(post.getlist("sections"))


def admin_create(request):
    _guard(request)
    back = HttpResponseRedirect(reverse("admin:super_admins"))
    if request.method != "POST":
        return back
    User = get_user_model()
    username = (request.POST.get("username") or "").strip()
    password = request.POST.get("password") or ""
    role = request.POST.get("role") if request.POST.get("role") in (roles.SUPER, roles.ADMIN) else roles.ADMIN
    tenants = [x for x in request.POST.getlist("tenants") if x in known_tenants()]
    if not username or User.objects.filter(username__iexact=username).exists():
        messages.error(request, t("Login bo‘sh yoki band."))
        return back
    if role == roles.ADMIN and not tenants:
        messages.error(request, t("Adminga kamida bitta bo‘lim biriktiring."))
        return back
    if _sections_missing(role, request.POST):
        messages.error(request, t("Kamida bitta sahifaga ruxsat bering."))
        return back
    candidate = User(username=username, first_name=(request.POST.get("name") or "").strip()[:150])
    try:
        validate_password(password, candidate)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return back
    candidate.set_password(password)
    candidate.save()
    profile = _apply_role(candidate, role, tenants, request.POST)
    profile.phone = (request.POST.get("phone") or "").strip()[:32]
    profile.note = (request.POST.get("note") or "").strip()[:255]
    profile.save()
    messages.success(request, f"{t('Yaratildi')}: {username}")
    return back


def admin_update(request):
    _guard(request)
    back = HttpResponseRedirect(reverse("admin:super_admins"))
    if request.method != "POST":
        return back
    User = get_user_model()
    user = User.objects.filter(pk=request.POST.get("id"), is_staff=True).first()
    if user is None:
        messages.error(request, t("Foydalanuvchi topilmadi."))
        return back
    action = request.POST.get("action")
    is_self = user.pk == request.user.pk
    if action in ("block", "demote") and is_self:
        messages.error(request, t("O‘zingizni bloklay yoki pasaytira olmaysiz."))
        return back
    if action == "block":
        user.is_active = False
        user.save()
        _close_sessions({user.pk})
        messages.success(request, f"{t('Bloklandi')}: {user.username}")
    elif action == "unblock":
        user.is_active = True
        user.save()
        messages.success(request, f"{t('Blokdan chiqarildi')}: {user.username}")
    elif action == "sessions":
        n = _close_sessions({user.pk}, keep_key=request.session.session_key or "")
        messages.success(request, f"{t('Yopilgan sessiyalar')}: {n}")
    elif action == "password":
        password = request.POST.get("password") or ""
        try:
            validate_password(password, user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return back
        user.set_password(password)
        user.save()
        if is_self:
            update_session_auth_hash(request, user)
        else:
            _close_sessions({user.pk})
        messages.success(request, f"{t('Parol almashtirildi')}: {user.username}")
    elif action == "edit":
        role = request.POST.get("role") if request.POST.get("role") in (roles.SUPER, roles.ADMIN) else roles.ADMIN
        if is_self and role != roles.SUPER:
            messages.error(request, t("O‘zingizni pasaytira olmaysiz."))
            return back
        tenants = [x for x in request.POST.getlist("tenants") if x in known_tenants()]
        if role == roles.ADMIN and not tenants:
            messages.error(request, t("Adminga kamida bitta bo‘lim biriktiring."))
            return back
        if _sections_missing(role, request.POST):
            messages.error(request, t("Kamida bitta sahifaga ruxsat bering."))
            return back
        if role == roles.ADMIN and user.is_superuser and not User.objects.filter(
            is_superuser=True, is_active=True
        ).exclude(pk=user.pk).exists():
            messages.error(request, t("Oxirgi super adminni pasaytirib bo‘lmaydi."))
            return back
        profile = _apply_role(user, role, tenants, request.POST)
        profile.phone = (request.POST.get("phone") or "").strip()[:32]
        profile.note = (request.POST.get("note") or "").strip()[:255]
        profile.save()
        user.first_name = (request.POST.get("name") or "").strip()[:150]
        user.save()
        messages.success(request, f"{t('Saqlandi')}: {user.username}")
    return back


def audit(request):
    _guard(request)
    qs = AuditEvent.objects.select_related("user")
    who = (request.GET.get("who") or "").strip()
    action = (request.GET.get("action") or "").strip()
    tenant = (request.GET.get("tenant") or "").strip()
    days = request.GET.get("days") or "30"
    if who:
        qs = qs.filter(username=who)
    if action:
        qs = qs.filter(action=action)
    if tenant:
        qs = qs.filter(tenant=tenant)
    if days.isdigit():
        qs = qs.filter(at__gte=timezone.now() - timedelta(days=int(days)))
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="jurnal.csv"'
        response.write("﻿")
        w = csv.writer(response)
        w.writerow(["sana", "foydalanuvchi", "bo‘lim", "harakat", "tafsilot", "ip"])
        for e in qs[:20000]:
            w.writerow([timezone.localtime(e.at).strftime("%Y-%m-%d %H:%M:%S"),
                        e.username, e.tenant, e.action, e.detail, e.ip or ""])
        return response
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    keep = request.GET.copy()
    keep.pop("page", None)
    return TemplateResponse(request, "admin/super/audit.html", _ctx(
        request, "Jurnal", page=page, who=who, action=action, tenant=tenant, days=days,
        users=AuditEvent.objects.order_by().values_list("username", flat=True).distinct(),
        actions=AuditEvent.objects.order_by().values_list("action", flat=True).distinct(),
        all_tenants=known_tenants(), querystring=keep.urlencode(),
    ))


def security(request):
    _guard(request)
    from django.conf import settings as dj

    User = get_user_model()
    day = timezone.now() - timedelta(days=1)
    sessions = _active_sessions()
    min_len = 8
    for v in dj.AUTH_PASSWORD_VALIDATORS:
        if v["NAME"].endswith("MinimumLengthValidator"):
            min_len = v.get("OPTIONS", {}).get("min_length", 8)
    stats = {
        "supers": User.objects.filter(is_superuser=True, is_active=True).count(),
        "admins": User.objects.filter(is_staff=True, is_superuser=False, is_active=True).count(),
        "blocked": User.objects.filter(is_staff=True, is_active=False).count(),
        "sessions": sum(sessions.values()),
        "failed_24h": AuditEvent.objects.filter(action=LOGIN_FAILED, at__gte=day).count(),
        "logins_24h": AuditEvent.objects.filter(action=LOGIN, at__gte=day).count(),
    }
    policies = [
        f"{t('Parol kamida')} {min_len} {t('belgi, oddiy va faqat raqamdan iborat parollar rad etiladi')}",
        t("Parollar shifrlangan holda saqlanadi (PBKDF2)"),
        f"{t('Sessiya')} {dj.SESSION_COOKIE_AGE // 3600} {t('soat amal qiladi')}",
        t("Bloklash va sessiyani yopish darhol ishlaydi"),
        t("Admin faqat o‘ziga biriktirilgan bo‘limni ko‘radi; bot qoidalari, sozlamalar, restart va jadvallar — faqat super admin"),
        t("Har bir o‘zgarish va kirish urinishi jurnalga IP bilan yoziladi"),
        t("Kalitlar va tokenlar panelda ko‘rsatilmaydi"),
    ]
    failed = AuditEvent.objects.filter(action=LOGIN_FAILED).order_by("-at")[:15]
    return TemplateResponse(request, "admin/super/security.html", _ctx(
        request, "Xavfsizlik", stats=stats, policies=policies, failed=failed,
    ))


def close_sessions(request):
    _guard(request)
    if request.method == "POST":
        User = get_user_model()
        ids = set(User.objects.exclude(pk=request.user.pk).values_list("pk", flat=True))
        n = _close_sessions(ids)
        messages.success(request, f"{t('Yopilgan sessiyalar')}: {n}")
    return HttpResponseRedirect(reverse("admin:super_security"))


def backups_page(request):
    _guard(request)
    return TemplateResponse(request, "admin/super/backups.html", _ctx(
        request, "Zaxira nusxalar", backup=backups(),
    ))
