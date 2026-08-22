from __future__ import annotations

import hashlib
import secrets

from flask import Response

SESSION_COOKIE_NAME = "venue_session"
SESSION_TOKEN_BYTES = 32
ADMIN_SESSION_SECONDS = 12 * 60 * 60
MAX_PASSWORD_LENGTH = 1024

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def new_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def digest_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=ADMIN_SESSION_SECONDS,
        httponly=True,
        secure=secure,
        samesite="Lax",
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        "",
        max_age=0,
        httponly=True,
        secure=secure,
        samesite="Lax",
        path="/",
    )


def security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response
