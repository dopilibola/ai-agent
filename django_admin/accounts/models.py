"""Who may do what in the panel, and what they did.

Both tables live in the panel's own SQLite (`default`), next to Django's users
and sessions — they are facts about panel logins, not about the bots, and the
shared Postgres stays Alembic's alone (`crm/routers.py`).

Two roles, mirroring the split operators asked for:

* **Super admin** — a Django superuser. Sees every tenant and the `/admin/super/`
  section: tenants and their bot switch, admins, the action log, security,
  backups, and the pages that can break a working bot (prompts, `.env`
  settings, unit restarts, raw tables).
* **Admin** — a staff user with an `AdminProfile` naming the tenants they work
  for and the *sections* (pages) a super admin granted them, optionally view
  only. See `roles.SECTIONS`.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class AdminProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="panel_profile"
    )
    # Comma-separated tenant ids ("maskan,anfa"). Empty means no tenant — an
    # admin nobody assigned should see nothing rather than everything.
    tenants = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    # Comma-separated section keys from `roles.SECTIONS` — which pages this
    # admin may open. The default is what every admin could open before
    # sections existed, so the migration changes nobody's access.
    sections = models.CharField(
        max_length=255, blank=True,
        default="dashboard,users,reply,export,outreach,learning,analytics,health",
    )
    # View only: the admin may open their sections but every POST is refused.
    readonly = models.BooleanField(default=False)

    def tenant_list(self) -> list[str]:
        return [t.strip() for t in self.tenants.split(",") if t.strip()]

    def section_list(self) -> list[str]:
        return [t.strip() for t in self.sections.split(",") if t.strip()]

    def __str__(self) -> str:
        return f"{self.user} → {self.tenants or '—'}"


class AuditEvent(models.Model):
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    # Kept separately so a deleted user's history still says who it was, and a
    # failed login (no user) still records the name that was tried.
    username = models.CharField(max_length=150, blank=True, default="")
    action = models.CharField(max_length=64, db_index=True)
    tenant = models.CharField(max_length=64, blank=True, default="")
    detail = models.CharField(max_length=500, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ("-at",)

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M} {self.username} {self.action}"
