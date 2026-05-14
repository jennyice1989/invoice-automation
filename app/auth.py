"""
Single-password authentication via signed cookies.

Not enterprise auth — just enough to keep the URL from being walk-in-able.
The password lives in env (APP_PASSWORD). Successful login sets a signed
cookie that expires in 30 days.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Annotated

from fastapi import Cookie, HTTPException, status
from fastapi.responses import RedirectResponse

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
COOKIE_SECRET = os.environ.get("COOKIE_SECRET", "")
COOKIE_NAME = "session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days


def _ensure_secret() -> str:
    """Use COOKIE_SECRET if set, else derive from APP_PASSWORD.

    The derived secret is stable across restarts (unlike a random one),
    so cookies don't get invalidated on every deploy.
    """
    if COOKIE_SECRET:
        return COOKIE_SECRET
    if APP_PASSWORD:
        return hashlib.sha256(
            ("cookie-salt::" + APP_PASSWORD).encode()
        ).hexdigest()
    return ""


def make_token() -> str:
    """Create a signed token: <timestamp>.<hmac>."""
    secret = _ensure_secret()
    if not secret:
        raise RuntimeError("APP_PASSWORD not configured")
    ts = str(int(time.time()))
    sig = hmac.new(
        secret.encode(), ts.encode(), hashlib.sha256
    ).hexdigest()
    return f"{ts}.{sig}"


def verify_token(token: str) -> bool:
    secret = _ensure_secret()
    if not secret or not token or "." not in token:
        return False
    ts, sig = token.split(".", 1)
    expected = hmac.new(
        secret.encode(), ts.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        issued = int(ts)
    except ValueError:
        return False
    if time.time() - issued > COOKIE_MAX_AGE:
        return False
    return True


def check_password(supplied: str) -> bool:
    if not APP_PASSWORD or not supplied:
        return False
    # Constant-time comparison.
    return hmac.compare_digest(APP_PASSWORD, supplied)


def require_auth(
    session: Annotated[str | None, Cookie()] = None,
) -> None:
    """FastAPI dependency: 401 if not authenticated."""
    if not APP_PASSWORD:
        # Auth disabled (single-user dev mode).
        return
    if not session or not verify_token(session):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )


def require_auth_html(
    session: Annotated[str | None, Cookie()] = None,
):
    """Variant for HTML routes: redirect to /login instead of 401."""
    if not APP_PASSWORD:
        return None
    if not session or not verify_token(session):
        return RedirectResponse(url="/login", status_code=303)
    return None


# CSRF: trivial — single password app, all writes require the auth cookie
# (which is httpOnly + SameSite=Lax), so CSRF surface is minimal.
def random_csrf_seed() -> str:
    return secrets.token_urlsafe(16)
