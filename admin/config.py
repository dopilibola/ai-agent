"""Admin panel configuration — env-driven, in the same style as the tenants.

`load_dotenv()` runs first so `.env` populates the environment. Secrets
(`ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`) have no usable default; `server.run`
refuses to start until they're set, but importing this module never fails so
`--help`/tests stay cheap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

_DAY = 60 * 60 * 24


def _csv(raw: str | None) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


@dataclass(frozen=True)
class AdminConfig:
    host: str = field(default_factory=lambda: os.environ.get("ADMIN_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("ADMIN_PORT", "58210")))

    # Single internal account.
    username: str = field(default_factory=lambda: os.environ.get("ADMIN_USERNAME", "admin"))
    password: str = field(default_factory=lambda: os.environ.get("ADMIN_PASSWORD", ""))

    # Signed-cookie session.
    session_secret: str = field(
        default_factory=lambda: os.environ.get("ADMIN_SESSION_SECRET", "")
    )
    session_max_age: int = field(
        default_factory=lambda: int(os.environ.get("ADMIN_SESSION_MAX_AGE", str(_DAY)))
    )
    cookie_name: str = field(
        default_factory=lambda: os.environ.get("ADMIN_COOKIE_NAME", "ai_sales_admin")
    )
    # Set true when served over HTTPS (production behind a TLS reverse proxy).
    cookie_secure: bool = field(
        default_factory=lambda: os.environ.get("ADMIN_COOKIE_SECURE", "false").lower()
        == "true"
    )

    # Browser origins allowed to call the API directly (CORS). In dev the Vite
    # proxy makes calls same-origin, so this mostly matters for direct access.
    cors_origins: list[str] = field(
        default_factory=lambda: _csv(os.environ.get("ADMIN_CORS_ORIGINS"))
        or ["http://localhost:5173"]
    )


config = AdminConfig()
