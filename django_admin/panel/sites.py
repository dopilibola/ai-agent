"""The panel's AdminSite: the model admin plus four pages of its own.

The stock admin index is a list of tables, which answers "what exists" — not
"how is it going". This site replaces the index with a dashboard and adds three
more pages that the model admin cannot express:

* ``/admin/users/`` — one row per chat, ranked by how close they are to buying,
  with the category that percentage falls into. Also the home of the three
  tables Django cannot register (see ``crm/admin/shared.py``).
* ``/admin/users/<tenant>/<chat_id>/`` — one customer: their conversation, why
  their score is what it is, a link that opens the same chat in Telegram, and
  the two operator actions — set the category (active / bought / closed) and
  schedule one message to them a month out.
* ``/admin/outreach/`` — the automatic messages: the pacing rules, the queue,
  and what actually went out.
* ``/admin/prompts/`` — the agents' own rules, edited on disk; the bots re-read
  them on every reply, so a save is live without a restart.
* ``/admin/health/`` — are the units running, is the corpus still being written,
  and a restart button for the bot units.
* ``/admin/settings/`` — the few `.env` values an operator legitimately changes.
* ``/admin/learning/`` — teach the agent: upload real sales chats, write down
  experience, filter and approve the pairs it retrieves (`crm/learning.py`).
* ``/admin/analytics/`` — the trends, the touch mix, and per-tool failure rates.
* ``/admin/thread/<thread_id>/`` — one conversation in order.

The views themselves live in `panel.views`; this module only wires them up and
keeps the navigation in one place.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import path, reverse
from django.utils.html import format_html


class PanelAdminSite(admin.AdminSite):
    site_title = "AI Sales"
    site_header = "AI Sales"
    index_title = "Bosh sahifa"
    # The panel draws its own side menu; Django's model sidebar would be a second one.
    enable_nav_sidebar = False

    def get_urls(self):
        from panel import super_views as sv
        from panel import views

        superadmin = [
            path("super/", self.admin_view(sv.home), name="super_home"),
            path("super/tenants/<str:tenant>/", self.admin_view(sv.tenant_detail), name="super_tenant"),
            path("super/tenants-bot/", self.admin_view(sv.tenant_bot), name="super_tenant_bot"),
            path("super/admins/", self.admin_view(sv.admins), name="super_admins"),
            path("super/admins/create/", self.admin_view(sv.admin_create), name="super_admin_create"),
            path("super/admins/update/", self.admin_view(sv.admin_update), name="super_admin_update"),
            path("super/audit/", self.admin_view(sv.audit), name="super_audit"),
            path("super/security/", self.admin_view(sv.security), name="super_security"),
            path("super/security/sessions/", self.admin_view(sv.close_sessions), name="super_sessions"),
            path("super/backups/", self.admin_view(sv.backups_page), name="super_backups"),
        ]
        custom = superadmin + [
            path("users/", self.admin_view(views.users), name="users"),
            path("users/export.csv", self.admin_view(views.users_csv), name="users_csv"),
            path("users/unmute/", self.admin_view(views.unmute), name="chat_unmute"),
            path(
                "users/<str:tenant>/<int:chat_id>/",
                self.admin_view(views.user_detail),
                name="user_detail",
            ),
            path(
                "users/state/",
                self.admin_view(views.set_state),
                name="set_state",
            ),
            path(
                "users/reply/",
                self.admin_view(views.operator_reply),
                name="operator_reply",
            ),
            path(
                "users/followup/",
                self.admin_view(views.schedule_followup),
                name="schedule_followup",
            ),
            path("outreach/", self.admin_view(views.outreach), name="outreach"),
            path(
                "outreach/save/",
                self.admin_view(views.outreach_save),
                name="outreach_save",
            ),
            path(
                "outreach/task/",
                self.admin_view(views.outreach_task),
                name="outreach_task",
            ),
            path(
                "outreach/stop/",
                self.admin_view(views.outreach_stop_chat),
                name="outreach_stop_chat",
            ),
            path("prompts/", self.admin_view(views.prompts), name="prompts"),
            path(
                "prompts/save/",
                self.admin_view(views.prompts_save),
                name="prompts_save",
            ),
            path("health/", self.admin_view(views.health), name="health"),
            path(
                "health/restart/",
                self.admin_view(views.health_restart),
                name="health_restart",
            ),
            path("settings/", self.admin_view(views.settings_env), name="settings_env"),
            path(
                "settings/save/",
                self.admin_view(views.settings_env_save),
                name="settings_env_save",
            ),
            path("learning/", self.admin_view(views.learning), name="learning"),
            path(
                "learning/import/",
                self.admin_view(views.learning_import),
                name="learning_import",
            ),
            path(
                "learning/manual/",
                self.admin_view(views.learning_manual),
                name="learning_manual",
            ),
            path(
                "learning/harvest/",
                self.admin_view(views.learning_harvest),
                name="learning_harvest",
            ),
            path(
                "learning/review/",
                self.admin_view(views.learning_review),
                name="learning_review",
            ),
            path(
                "learning/settings/",
                self.admin_view(views.learning_settings),
                name="learning_settings",
            ),
            path("analytics/", self.admin_view(views.analytics), name="analytics"),
            path(
                "thread/<path:thread_id>/",
                self.admin_view(views.thread),
                name="conversation_thread",
            ),
        ]
        return custom + super().get_urls()

    def index(self, request, extra_context=None):
        from panel import views

        return views.dashboard(request)

    def each_context(self, request):
        context = super().each_context(request)
        # Rendered by `admin/base_site.html` as the panel's top navigation. The
        # custom pages are not models, so nothing else would ever link to them.
        from accounts import roles

        is_super = roles.is_super(request.user)
        granted = roles.sections_of(request.user) if request.user.is_authenticated else ()
        # (section, label, url name, icon). Capabilities (reply, export,
        # restart) are sections without a page, so they are not listed.
        pages = [
            ("dashboard", "Bosh sahifa", "admin:index", "home"),
            ("users", "Mijozlar", "admin:users", "users"),
            ("outreach", "Avto-xabarlar", "admin:outreach", "send"),
            ("learning", "O‘qitish", "admin:learning", "book"),
            ("analytics", "Tahlil", "admin:analytics", "chart"),
            ("health", "Holat", "admin:health", "pulse"),
            ("prompts", "Bot qoidalari", "admin:prompts", "script"),
            ("settings", "Sozlamalar", "admin:settings_env", "gear"),
        ]
        nav = [(label, reverse(name), ico) for key, label, name, ico in pages if key in granted]
        if is_super:
            nav.append(("Jadvallar", reverse("admin:crm_conversationevent_changelist"), "table"))
        context["panel_nav"] = nav
        context["panel_role"] = roles.role_of(request.user) if request.user.is_authenticated else ""
        context["panel_sections"] = granted
        context["panel_missing"] = [k for k in roles.SECTION_KEYS if k not in granted]
        context["panel_can_edit"] = roles.can_edit(request.user) if request.user.is_authenticated else False
        context["super_nav"] = [
            ("Umumiy", reverse("admin:super_home"), "shield"),
            ("Adminlar va ruxsatlar", reverse("admin:super_admins"), "key"),
            ("Jurnal", reverse("admin:super_audit"), "list"),
            ("Xavfsizlik", reverse("admin:super_security"), "lock"),
            ("Zaxira nusxalar", reverse("admin:super_backups"), "archive"),
        ] if is_super else []
        context["panel_path"] = request.path
        return context


def thread_link(thread_id: str, label: str | None = None):
    """A link from any changelist row into the transcript view."""
    if not thread_id:
        return "—"
    url = reverse("admin:conversation_thread", args=[thread_id])
    return format_html('<a href="{}">{}</a>', url, label or thread_id)


def user_link(tenant_id: str, chat_id: int | None, label: str | None = None):
    """A link from any changelist row into that chat's detail page."""
    if not tenant_id or chat_id is None:
        return "—"
    url = reverse("admin:user_detail", args=[tenant_id, chat_id])
    return format_html('<a href="{}">{}</a>', url, label or chat_id)
