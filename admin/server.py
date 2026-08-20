"""Console entrypoint for the admin API (`admin-api`).

Fails fast with a clear message if the required secrets or DATABASE_URL are
missing, then hands off to uvicorn with the app factory.
"""

from __future__ import annotations

import sys


def run() -> None:
    from admin.config import config
    from db.engine import database_configured

    missing = [
        name
        for name, value in (
            ("ADMIN_PASSWORD", config.password),
            ("ADMIN_SESSION_SECRET", config.session_secret),
        )
        if not value
    ]
    if missing:
        sys.stderr.write(
            "admin-api: refusing to start; set "
            + ", ".join(missing)
            + " in the environment (.env).\n"
        )
        raise SystemExit(2)

    if not database_configured():
        sys.stderr.write(
            "admin-api: DATABASE_URL is not set; the admin panel requires Postgres.\n"
        )
        raise SystemExit(2)

    import uvicorn

    uvicorn.run(
        "admin.app:create_app",
        factory=True,
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
