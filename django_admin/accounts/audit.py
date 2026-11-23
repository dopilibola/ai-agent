"""Writing the action log. Never raises — a log write must not fail a request."""

from __future__ import annotations

import logging

from .roles import client_ip

logger = logging.getLogger(__name__)

LOGIN = "Panelga kirdi"
LOGOUT = "Paneldan chiqdi"
LOGIN_FAILED = "Kirish muvaffaqiyatsiz"


def record(request, action: str, *, tenant: str = "", detail: str = "", user=None,
           username: str = "") -> None:
    try:
        from .models import AuditEvent

        user = user if user is not None else getattr(request, "user", None)
        if user is not None and not getattr(user, "is_authenticated", False):
            user = None
        AuditEvent.objects.create(
            user=user,
            username=username or (user.get_username() if user else ""),
            action=action[:64],
            tenant=(tenant or "")[:64],
            detail=(detail or "")[:500],
            ip=client_ip(request) if request is not None else None,
        )
    except Exception:
        logger.warning("audit write failed", exc_info=True)
