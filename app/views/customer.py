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
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.access_codes import access_code_digest, is_valid_access_code
from app.db import get_session
from app.models import Booking, WebSession
from app.security import (
    CUSTOMER_SESSION_SECONDS,
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    digest_session_token,
    new_session_token,
    set_session_cookie,
)
from app.times import naive_utc

bp = Blueprint("customer", __name__, url_prefix="/customer")
logger = logging.getLogger("venue_inventory.customer")

ACCESS_CODE_FAILED = "Access code not recognized."
INVALID_LOOKUP_VALUE = "!" * 12


def customer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        session = getattr(g, "web_session", None)
        if session is None or session.actor_type != "booking":
            clear_cookie = session is None and bool(
                request.cookies.get(SESSION_COOKIE_NAME)
            )
            return _redirect_to_login(clear_cookie=clear_cookie)
        booking = get_session().get(Booking, session.booking_id)
        if booking is None:
            return _redirect_to_login(clear_cookie=True)
        return view(booking=booking, *args, **kwargs)

    return wrapped


@bp.get("/")
@bp.get("/portal")
@customer_required
def portal(*, booking: Booking):
    return render_template("customer/portal.html", booking=booking)


@bp.get("/login")
def login():
    if _is_customer():
        return redirect(url_for("customer.portal"))
    return _render_login()


@bp.post("/login")
def login_submit():
    if _is_customer():
        return redirect(url_for("customer.portal"))

    limiter = current_app.extensions["rate_limiter"]
    key = _rate_limit_key()
    blocked = limiter.is_blocked(key)

    entered_code = request.form.get("access_code")
    if entered_code is None:
        entered_code = request.form.get("code", "")
    lookup_value = (
        entered_code if is_valid_access_code(entered_code) else INVALID_LOOKUP_VALUE
    )
    config = current_app.config["APP_CONFIG"]
    digest = access_code_digest(lookup_value, config.access_code_hmac_secret)
    booking = (
        get_session()
        .execute(select(Booking).where(Booking.access_code_digest == digest))
        .scalar_one_or_none()
    )
    if blocked or booking is None:
        if not blocked:
            limiter.record_failure(key)
        logger.info(
            "Customer access-code sign-in failed.",
            extra={"event": "customer_login_failed", "client_ip": _client_ip()},
        )
        return _render_login(error=ACCESS_CODE_FAILED)

    limiter.reset(key)
    clock = current_app.extensions["clock"]
    now = naive_utc(clock.now())
    token = new_session_token()
    db_session = get_session()
    db_session.add(
        WebSession(
            session_digest=digest_session_token(token),
            actor_type="booking",
            booking_id=booking.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=CUSTOMER_SESSION_SECONDS),
        )
    )
    try:
        db_session.commit()
    except SQLAlchemyError:
        db_session.rollback()
        logger.exception(
            "Customer session could not be created.",
            extra={"event": "customer_session_create_failed"},
        )
        return _render_login(error=ACCESS_CODE_FAILED)

    logger.info(
        "Customer signed in to booking.",
        extra={"event": "customer_login_success", "client_ip": _client_ip()},
    )
    response = redirect(url_for("customer.portal"))
    set_session_cookie(
        response,
        token,
        secure=config.session_cookie_secure,
        max_age=CUSTOMER_SESSION_SECONDS,
    )
    return response


@bp.post("/logout")
@customer_required
def logout(*, booking: Booking):
    del booking
    config = current_app.config["APP_CONFIG"]
    db_session = get_session()
    db_session.delete(g.web_session)
    db_session.commit()
    g.web_session = None
    logger.info("Customer signed out.", extra={"event": "customer_logout"})
    response = redirect(url_for("customer.login"))
    clear_session_cookie(response, secure=config.session_cookie_secure)
    return response


def _render_login(*, error: str | None = None):
    response = current_app.make_response(
        render_template("customer/login.html", error=error)
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _is_customer() -> bool:
    session = getattr(g, "web_session", None)
    return session is not None and session.actor_type == "booking"


def _redirect_to_login(*, clear_cookie: bool):
    response = redirect(url_for("customer.login"))
    if clear_cookie:
        config = current_app.config["APP_CONFIG"]
        clear_session_cookie(response, secure=config.session_cookie_secure)
    return response


def _client_ip() -> str:
    # ProxyFix changes remote_addr only when the operator explicitly trusts
    # the single upstream proxy hop.
    return request.remote_addr or "unknown"


def _rate_limit_key() -> str:
    return f"customer-login:{_client_ip()}"
