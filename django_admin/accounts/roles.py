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


# ------------------------------------------------------------------ sections
#
# What an admin may open is a set of *sections*, chosen per person by a super
# admin. A section is a group of URL prefixes under /admin/; the longest
# matching prefix decides, so "users/reply/" (writing to a customer) can be
# granted apart from "users/" (reading them). Three of them are capabilities
# rather than pages — reply, export, restart — and have no menu item.
#
# Everything that is not in this table stays the super admin's alone: the
# super-admin section, raw tables, Django's user admin and the training
# retrieval knobs.

SECTIONS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("dashboard", "Bosh sahifa", ("",), "Bugungi ish ro‘yxati va asosiy raqamlar"),
    ("users", "Mijozlar", ("users/", "thread/"), "Mijozlar ro‘yxati va ularning suhbatlari"),
    ("reply", "Mijozga yozish", ("users/reply/", "users/followup/", "users/state/", "users/unmute/"),
     "Javob yozish, xabar rejalashtirish, «sotib olgan» belgisi, AI'ni qayta yoqish"),
    ("export", "CSV yuklab olish", ("users/export.csv",), "Mijozlar ro‘yxatini telefonlari bilan yuklab olish"),
    ("outreach", "Avto-xabarlar", ("outreach/",), "Avtomatik xabarlar navbati va qoidalari"),
    ("learning", "O‘qitish", ("learning/",), "Chat yuklash, tajriba yozish, misollarni tasdiqlash"),
    ("analytics", "Tahlil", ("analytics/",), "Grafiklar, voronka, tushum"),
    ("health", "Holat", ("health/",), "Bot ishlayaptimi — xizmatlar holati"),
    ("restart", "Botni qayta ishga tushirish", ("health/restart/",), "Holat sahifasidagi restart tugmasi"),
    ("prompts", "Bot qoidalari", ("prompts/",), "Botning yozma qoidalarini tahrirlash — xato tahrir botni buzadi"),
    ("settings", "Sozlamalar", ("settings/",), "Telefon, operatorlar, xotira kunlari (.env)"),
)
SECTION_KEYS = tuple(s[0] for s in SECTIONS)
SECTION_LABELS = {s[0]: s[1] for s in SECTIONS}

# What an admin created before sections existed could open — and the default
# for a new one, so "create admin" without touching the boxes stays sensible.
DEFAULT_SECTIONS = ("dashboard", "users", "reply", "export", "outreach", "learning", "analytics", "health")

# Ready-made sets for the admin form.
PRESETS = {
    "operator": ("dashboard", "users", "reply", "learning"),
    "viewer": ("dashboard", "users", "analytics", "health"),
    "manager": DEFAULT_SECTIONS + ("restart",),
}

# Never an admin's, whatever is ticked.
ALWAYS_SUPER = ("super/", "crm/", "auth/", "accounts/", "learning/settings/")

# Pages every signed-in staff user needs.
OPEN_PATHS = ("login/", "logout/", "password_change/", "jsi18n/")


def section_for(rest: str) -> str | None:
    """The section a path (after /admin/) belongs to; None = not grantable."""
    best, best_len = None, -1
    for key, _label, prefixes, _help in SECTIONS:
        for p in prefixes:
            hit = rest == "" if p == "" else rest.startswith(p)
            if hit and len(p) > best_len:
                best, best_len = key, len(p)
    return best


def clean_sections(values) -> list[str]:
    return [k for k in SECTION_KEYS if k in set(values or ())]


def sections_of(user) -> tuple[str, ...]:
    """The sections a user may open, in menu order."""
    if is_super(user):
        return SECTION_KEYS
    profile = getattr(user, "panel_profile", None)
    try:
        return tuple(profile.section_list()) if profile else ()
    except Exception:
        return ()


def can_edit(user) -> bool:
    """False for a view-only admin: every POST is refused."""
    if is_super(user):
        return True
    profile = getattr(user, "panel_profile", None)
    try:
        return not (profile and profile.readonly)
    except Exception:
        return True
