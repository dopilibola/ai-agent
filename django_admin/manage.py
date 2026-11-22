#!/usr/bin/env python
"""Entry point for the Django admin panel.

Run from anywhere: `uv run --extra django python django_admin/manage.py <cmd>`,
or via the `django-panel` console script (see pyproject.toml) which wraps
`runserver` with the panel's defaults.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    # The panel imports nothing from the bot runtime, but it does read the same
    # .env as the bots (DATABASE_URL). Put the repo root on sys.path so
    # `panel.settings` resolves regardless of the working directory.
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "panel.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
