from __future__ import annotations

import logging
from datetime import timedelta
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from app.db import get_session
from app.models import WebSession
from app.passwords import verify_admin_password
from app.security import (
    ADMIN_SESSION_SECONDS,
    clear_session_cookie,
    digest_session_token,
    new_session_token,
    set_session_cookie,
)
from app.times import naive_utc

bp = Blueprint("admin", __name__, url_prefix="/admin")
logger = logging.getLogger("venue_inventory.admin")

SIGN_IN_FAILED = "Sign-in failed."


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        session = getattr(g, "web_session", None)
        if session is None or session.actor_type != "admin":
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)

    return wrapped


@bp.get("/login")
def login():
    if _is_admin():
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/login.html")


@bp.post("/login")
def login_submit():
    if _is_admin():
        return redirect(url_for("admin.dashboard"))

    config = current_app.config["APP_CONFIG"]
    limiter = current_app.extensions["rate_limiter"]
    clock = current_app.extensions["clock"]
    key = _rate_limit_key()

    if limiter.is_blocked(key):
        logger.info(
            "Administrator sign-in throttled.",
            extra={"event": "admin_login_throttled", "client_ip": _client_ip()},
        )
        return render_template("admin/login.html", error=SIGN_IN_FAILED), 429

    password = request.form.get("password", "")
    if not verify_admin_password(password, config.admin_password_hash):
        limiter.record_failure(key)
        logger.info(
            "Administrator sign-in failed.",
            extra={"event": "admin_login_failed", "client_ip": _client_ip()},
        )
        return render_template("admin/login.html", error=SIGN_IN_FAILED), 200

    limiter.reset(key)
    now = naive_utc(clock.now())
    token = new_session_token()
    db_session = get_session()
    db_session.add(
        WebSession(
            session_digest=digest_session_token(token),
            actor_type="admin",
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=ADMIN_SESSION_SECONDS),
        )
    )
    db_session.commit()
    logger.info(
        "Administrator signed in.",
        extra={"event": "admin_login_success", "client_ip": _client_ip()},
    )
    response = redirect(url_for("admin.dashboard"))
    set_session_cookie(response, token, secure=config.session_cookie_secure)
    return response


@bp.get("/")
@admin_required
def dashboard():
    return render_template("admin/dashboard.html")


@bp.post("/logout")
@admin_required
def logout():
    config = current_app.config["APP_CONFIG"]
    db_session = get_session()
    db_session.delete(g.web_session)
    db_session.commit()
    g.web_session = None
    logger.info("Administrator signed out.", extra={"event": "admin_logout"})
    response = redirect(url_for("admin.login"))
    clear_session_cookie(response, secure=config.session_cookie_secure)
    return response


def _is_admin() -> bool:
    session = getattr(g, "web_session", None)
    return session is not None and session.actor_type == "admin"


def _client_ip() -> str:
    # request.remote_addr is the TCP peer, or X-Forwarded-For when
    # VENUE_INVENTORY_TRUST_PROXY=true (ProxyFix). Untrusted forwarded
    # headers are ignored so clients cannot reset the login limiter.
    return request.remote_addr or "unknown"


def _rate_limit_key() -> str:
    return f"admin-login:{_client_ip()}"
