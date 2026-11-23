"""Settings for the Django admin panel.

Two databases, on purpose:

* ``default`` — a local SQLite file holding *only* Django's own tables (users,
  sessions, admin log). Keeping them here means the panel never migrates
  anything into the shared Postgres: Alembic stays the single owner of that
  schema (`db/migrations/`).
* ``ai_sales`` — the shared Postgres the bots use, read and written through
  **unmanaged** models (``managed = False``) that map the existing tables.
  ``crm.routers.AiSalesRouter`` sends every ``crm`` model there and refuses to
  migrate it.

So `manage.py migrate` is safe to run at any time: it only ever touches the
SQLite file.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

# The bots' .env is the source of truth for DATABASE_URL; load it if present so
# the panel needs no config of its own for a local run.
try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is a base dep, but don't hard-fail on it
    pass
else:
    load_dotenv(REPO_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = _env_bool("DJANGO_DEBUG", False)

# A dev fallback keeps `manage.py` usable out of the box; a non-debug run must
# supply a real key rather than silently signing sessions with a public string.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise SystemExit(
            "DJANGO_SECRET_KEY is not set. Generate one and put it in .env:\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(50))\""
        )
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if h.strip()
]
# Behind nginx+TLS the browser origin is https://…, which Django checks on POST.
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

INSTALLED_APPS = [
    "panel.apps.PanelAdminConfig",  # replaces django.contrib.admin (custom site)
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "crm",
    "accounts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves /static/ from the panel process itself, so the whole panel is one
    # port behind nginx — no separate static-file location to keep in sync.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Between session and common, as Django requires: it needs the session to
    # find the chosen language and must run before URLs are resolved.
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Admin vs super admin, tenant scoping and the action log (accounts/).
    "accounts.middleware.RoleMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "panel.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "panel" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "panel.wsgi.application"


def _postgres_config(url: str) -> dict:
    """Turn a SQLAlchemy-style DATABASE_URL into Django DATABASES options.

    The bots use `postgresql+psycopg://…` (SQLAlchemy needs the driver suffix);
    Django wants a plain scheme and its own ENGINE, so the `+driver` part is
    dropped here rather than asking for a second env var.
    """
    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": (parsed.path or "/").lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"connect_timeout": 10},
    }


DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise SystemExit(
        "DATABASE_URL is not set — the panel reads the same Postgres as the bots."
    )

DATABASES = {
    # Django's own tables only. Never the shared Postgres: see the module docstring.
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("DJANGO_PANEL_DB", str(BASE_DIR / "panel.sqlite3")),
    },
    "ai_sales": _postgres_config(DATABASE_URL),
}

DATABASE_ROUTERS = ["crm.routers.AiSalesRouter"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# The panel is bilingual. Uzbek is the default and the source language of the
# panel's own copy (`crm/i18n.py`); Django's own strings — the login form, the
# model-admin pages — switch through the catalogues it already ships for both.
LANGUAGE_CODE = os.getenv("DJANGO_LANGUAGE_CODE", "uz")
LANGUAGES = [("uz", "O‘zbekcha"), ("en", "English")]
LANGUAGE_COOKIE_NAME = "ai_sales_panel_language"
# The clinic/cemetery clocks the bots reason in — keep the panel on the same one
# so a timestamp here matches what an operator saw in Telegram.
TIME_ZONE = os.getenv("TZ_NAME", "Asia/Tashkent")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# `panel` is not an installed app (PanelAdminConfig replaces django.contrib.admin),
# so its static dir is listed explicitly rather than found by the app finder.
STATICFILES_DIRS = [BASE_DIR / "panel" / "static"]
# Serve straight from the app static dirs, so the panel works before anyone runs
# `collectstatic`. Run collectstatic anyway in production — it is faster — but a
# deploy that forgets it gets a styled admin rather than an unstyled one.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG

SESSION_COOKIE_NAME = "ai_sales_panel_sessionid"
# A panel login lasts a working day, not the two-week Django default: the panel
# can message customers and restart bots, and a forgotten laptop is the risk.
SESSION_COOKIE_AGE = int(os.getenv("DJANGO_SESSION_HOURS", "12")) * 3600
# Where `deploy/ai-sales-backup.sh` writes; the super admin's backups page lists it.
BACKUP_DIR = os.getenv("BACKUP_DIR", str(Path.home() / "backups" / "ai-sales"))
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SESSION_COOKIE_SECURE = _env_bool("DJANGO_SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = _env_bool("DJANGO_SECURE_COOKIES", True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
}
