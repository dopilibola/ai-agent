"""Role checks shared by the middleware, the views and the navigation."""

from __future__ import annotations

SUPER = "superadmin"
ADMIN = "admin"


def is_super(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.is_superuser)


def role_of(user) -> str:
    return SUPER if is_super(user) else ADMIN


def allowed_tenants(user) -> list[str] | None:
    """None = every tenant (super admin); otherwise the admin's own list."""
    if is_super(user):
        return None
    profile = getattr(user, "panel_profile", None)
    try:
        return profile.tenant_list() if profile else []
    except Exception:
        return []


def can_see_tenant(user, tenant: str) -> bool:
    allowed = allowed_tenants(user)
    return allowed is None or tenant in allowed


def client_ip(request) -> str | None:
    # nginx sets X-Forwarded-For; the panel only listens on 127.0.0.1, so the
    # header can only come from our own proxy.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return ip or None
