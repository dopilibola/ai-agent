"""Role enforcement and the action log, in one place.

Doing this as middleware rather than in each view is the point: the panel has
some thirty views and a stock model admin, and a rule that every view must
remember to check is a rule that one of them forgets.

For an **admin** (not a super admin):

* a page outside the sections a super admin granted them answers 403 (the
  home page instead sends them to their first section), and the super-admin
  section, raw tables and Django's user admin are never theirs
  (`roles.SECTIONS`, `roles.ALWAYS_SUPER`);
* a view-only admin gets 403 on every POST;
* tenant scoping: a list page opened without `?tenant=` is redirected to the
  admin's own tenant, any other tenant in a query, a form or a URL is 403, and
  tenant pickers only list the admin's tenants.

For everyone, every successful POST under `/admin/` is written to the action
log with who, what, which tenant and from which IP.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse

from . import roles
from .audit import record

ADMIN_PREFIX = "/admin/"

# Pages that show one tenant (or all of them) chosen by `?tenant=`.
SCOPED_PAGES = {
    "",
    "users/",
    "users/export.csv",
    "outreach/",
    "analytics/",
    "learning/",
}

# url_name → what the log calls it.
ACTIONS = {
    "set_state": "Mijoz holatini o‘zgartirdi",
    "operator_reply": "Mijozga javob yozdi",
    "schedule_followup": "Xabar rejalashtirdi",
    "chat_unmute": "AI'ni qayta yoqdi",
    "outreach_save": "Avto-xabar qoidalarini o‘zgartirdi",
    "outreach_task": "Navbatdagi xabarni o‘zgartirdi",
    "outreach_stop_chat": "Mijozga avto-xabarni to‘xtatdi",
    "prompts_save": "Bot qoidasini tahrirladi",
    "health_restart": "Xizmatni qayta ishga tushirdi",
    "settings_env_save": "Sozlamalarni o‘zgartirdi",
    "learning_import": "Chat yukladi",
    "learning_manual": "Tajriba yozdi",
    "learning_harvest": "Misollar yig‘di",
    "learning_review": "Misollarni ko‘rib chiqdi",
    "learning_settings": "O‘qitish sozlamasini o‘zgartirdi",
    "super_tenant_bot": "Botni yoqdi/o‘chirdi",
    "super_admin_create": "Admin yaratdi",
    "super_admin_update": "Adminni o‘zgartirdi",
    "super_sessions": "Sessiyalarni yopdi",
}

# Form fields worth keeping in the log. Never passwords, never message text.
_DETAIL_KEYS = ("action", "id", "chat_id", "key", "kind", "status", "unit", "enabled",
                "username", "role", "days", "seller", "label")


# Where each page section lives, for sending an admin to the first one they have.
SECTION_URLS = {
    "dashboard": "admin:index", "users": "admin:users", "outreach": "admin:outreach",
    "learning": "admin:learning", "analytics": "admin:analytics", "health": "admin:health",
    "prompts": "admin:prompts", "settings": "admin:settings_env",
}


def _first_page(granted) -> str | None:
    from django.urls import reverse

    for key in granted:
        if key in SECTION_URLS:
            return reverse(SECTION_URLS[key])
    return None


def _forbidden(request, reason: str):
    from django.contrib import admin

    context = {**admin.site.each_context(request), "title": "403", "reason": reason}
    response = TemplateResponse(request, "admin/forbidden.html", context, status=403)
    return response.render()


class RoleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        user = getattr(request, "user", None)
        if (
            path.startswith(ADMIN_PREFIX)
            and user is not None
            and user.is_authenticated
            and user.is_staff
            and not roles.is_super(user)
        ):
            blocked = self._check(request, path[len(ADMIN_PREFIX):])
            if blocked is not None:
                return blocked
        response = self.get_response(request)
        self._audit(request, response)
        return response

    # ---------------------------------------------------------------- scoping

    def _check(self, request, rest: str):
        if rest.startswith(roles.OPEN_PATHS):
            return None
        if rest.startswith(roles.ALWAYS_SUPER):
            return _forbidden(request, "Bu bo‘lim faqat super admin uchun.")
        granted = roles.sections_of(request.user)
        section = roles.section_for(rest)
        if section is None:
            return _forbidden(request, "Bu bo‘lim faqat super admin uchun.")
        if section not in granted:
            if section == "dashboard" and request.method == "GET":
                first = _first_page(granted)
                if first:
                    return HttpResponseRedirect(first)
            return _forbidden(request, "Bu bo‘limga sizga ruxsat berilmagan. Super adminga murojaat qiling.")
        if request.method == "POST" and not roles.can_edit(request.user):
            return _forbidden(request, "Sizda faqat ko‘rish huquqi bor — o‘zgartirish kiritib bo‘lmaydi.")
        allowed = roles.allowed_tenants(request.user) or []
        if not allowed:
            return _forbidden(request, "Sizga hali hech qaysi bo‘lim biriktirilmagan. Super adminga murojaat qiling.")

        for source in (request.GET, request.POST):
            tenant = (source.get("tenant") or "").strip()
            if tenant and tenant not in allowed:
                return _forbidden(request, "Bu bo‘lim sizga biriktirilmagan.")

        parts = rest.split("/")
        if rest.startswith("users/") and len(parts) >= 3 and parts[2].isdigit():
            if parts[1] not in allowed:
                return _forbidden(request, "Bu bo‘lim sizga biriktirilmagan.")
        if rest.startswith("thread/"):
            tenant = rest[len("thread/"):].split(":", 1)[0]
            if tenant not in allowed:
                return _forbidden(request, "Bu bo‘lim sizga biriktirilmagan.")

        if request.method == "GET" and rest in SCOPED_PAGES and not request.GET.get("tenant"):
            query = request.GET.copy()
            query["tenant"] = allowed[0]
            return HttpResponseRedirect(f"{request.path}?{query.urlencode()}")
        return None

    def process_template_response(self, request, response):
        """Tenant pickers list only the admin's own tenants."""
        user = getattr(request, "user", None)
        data = getattr(response, "context_data", None)
        if data and "tenants" in data and user is not None and not roles.is_super(user):
            allowed = roles.allowed_tenants(user) or []
            data["tenants"] = [t for t in (data["tenants"] or []) if t in allowed]
        return response

    # ------------------------------------------------------------------ audit

    def _audit(self, request, response):
        if request.method != "POST" or not request.path.startswith(ADMIN_PREFIX):
            return
        if response.status_code not in (200, 302):
            return
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return
        match = getattr(request, "resolver_match", None)
        name = match.url_name if match else ""
        if name in ("login", "logout", "set_language") or not name:
            return
        # Stock model-admin pages: name like crm_maskanorder_change.
        action = ACTIONS.get(name) or name.replace("_", " ")
        bits = []
        for key in _DETAIL_KEYS:
            value = request.POST.get(key)
            if value not in (None, ""):
                bits.append(f"{key}={str(value)[:60]}")
        ids = request.POST.getlist("ids")
        if ids:
            bits.append(f"ids={len(ids)}")
        if match and match.kwargs:
            bits += [f"{k}={v}" for k, v in match.kwargs.items()]
        tenant = request.POST.get("tenant") or (match.kwargs.get("tenant") if match else "") or ""
        record(request, action, tenant=tenant, detail=" ".join(bits))
