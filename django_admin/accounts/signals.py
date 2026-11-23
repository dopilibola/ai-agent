from __future__ import annotations

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .audit import LOGIN, LOGIN_FAILED, LOGOUT, record


@receiver(user_logged_in)
def _login(sender, request, user, **kwargs):
    record(request, LOGIN, user=user)


@receiver(user_logged_out)
def _logout(sender, request, user, **kwargs):
    if user is not None:
        record(request, LOGOUT, user=user)


@receiver(user_login_failed)
def _failed(sender, credentials, request=None, **kwargs):
    # Only the username — never anything else from the credentials dict.
    record(request, LOGIN_FAILED, username=str((credentials or {}).get("username", ""))[:150])
