"""Console sign-in with a username and password (or the admin token)."""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import SESSION_COOKIE, admin_ctx, check_password, make_session
from ..db import AdminUser, audit, get_db, utcnow
from ..permissions import ROLES

router = APIRouter(prefix="/admin/session", tags=["console"])

_failures: dict[str, list[float]] = defaultdict(list)
MAX_FAILURES, WINDOW = 5, 900


def _throttled(key: str) -> bool:
    now = time.time()
    _failures[key] = [t for t in _failures[key] if now - t < WINDOW]
    return len(_failures[key]) >= MAX_FAILURES


class Login(BaseModel):
    username: str
    password: str
    remember: bool = False


@router.post("/login")
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    key = f"{ip}|{body.username.lower()}"
    if _throttled(key):
        raise HTTPException(429, "too many attempts; try again in 15 minutes")
    user = db.get(AdminUser, body.username.strip().lower())
    if user is None or not check_password(body.password, user.password_hash):
        _failures[key].append(time.time())
        audit(db, "system", "admin.login_failed", detail=body.username.strip().lower()[:60])
        raise HTTPException(401, "wrong username or password")
    _failures.pop(key, None)
    value, max_age = make_session(user.username, body.remember)
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(SESSION_COOKIE, value, max_age=max_age, httponly=True, samesite="strict",
                        secure=https, path="/admin")
    user.last_login_at = utcnow()
    audit(db, f"admin:{user.username}", "admin.login", detail="remember 30 days" if body.remember else None)
    return {"username": user.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/admin")
    return {"ok": True}


@router.get("/me")
def me(request: Request, x_admin_token: str = Header(default=""), db: Session = Depends(get_db)):
    has_users = (db.scalar(select(func.count()).select_from(AdminUser)) or 0) > 0
    try:
        ctx = admin_ctx(request, x_admin_token)
    except HTTPException:
        return {"signed_in": False, "has_users": has_users}
    return {"signed_in": True, "username": ctx.username, "role": ctx.role, "role_label": ROLES[ctx.role]["label"],
            "permissions": sorted(ctx.perms), "has_users": has_users}
