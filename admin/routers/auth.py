"""Login / logout / session check for the single internal account."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from admin.security import RequireUser, clear_session, issue_session, verify_credentials

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginIn, response: Response) -> dict[str, str]:
    if not verify_credentials(body.username, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    issue_session(response, body.username)
    return {"username": body.username}


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    clear_session(response)
    return {"ok": True}


@router.get("/me")
async def me(user: str = RequireUser) -> dict[str, str]:
    return {"username": user}
