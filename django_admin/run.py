"""Console entrypoint: `django-panel` (see pyproject.toml [project.scripts]).

Serves the Django admin with uvicorn on one port, static files included
(WhiteNoise), so the panel is a single process to supervise — the same shape as
`admin-api`. `manage.py` remains the way to run management commands
(`migrate`, `createsuperuser`, `collectstatic`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def run() -> None:
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "panel.settings")

    import uvicorn

    uvicorn.run(
        "panel.asgi:application",
        host=os.getenv("DJANGO_PANEL_HOST", "127.0.0.1"),
        port=int(os.getenv("DJANGO_PANEL_PORT", "8200")),
        log_level=os.getenv("DJANGO_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    run()
