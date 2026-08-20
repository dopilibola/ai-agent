"""Cookie-session auth for the single internal admin account.

Login compares credentials (constant-time) against `ADMIN_USERNAME` /
`ADMIN_PASSWORD`, then issues an HttpOnly cookie holding a signed, timestamped
token (`itsdangerous`). `current_user` is the FastAPI dependency every
protected route depends on; it re-verifies the signature and TTL on each call.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from admin.config import config

_SALT = "ai-sales-admin-session"


def _serializer() -> URLSafeTimedSerializer:
    # If the secret is unset we still build a serializer (so imports never fail),
    # but server.run refuses to boot without ADMIN_SESSION_SECRET, so this
    # fallback is never used by a real process.
    return URLSafeTimedSerializer(config.session_secret or "dev-only-insecure", salt=_SALT)


def verify_credentials(username: str, password: str) -> bool:
    if not config.password:  # never authenticate against an empty password
        return False
    user_ok = secrets.compare_digest(username or "", config.username)
    pass_ok = secrets.compare_digest(password or "", config.password)
    return user_ok and pass_ok


def issue_session(response: Response, username: str) -> None:
    token = _serializer().dumps({"u": username})
    response.set_cookie(
        key=config.cookie_name,
        value=token,
        max_age=config.session_max_age,
        httponly=True,
        samesite="lax",
        secure=config.cookie_secure,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(config.cookie_name, path="/")


def current_user(request: Request) -> str:
    token = request.cookies.get(config.cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        data = _serializer().loads(token, max_age=config.session_max_age)
    except SignatureExpired as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired") from exc
    except BadSignature as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session") from exc
    return str(data.get("u", ""))


# Convenience: `user: str = RequireUser` on any protected route.
RequireUser = Depends(current_user)
