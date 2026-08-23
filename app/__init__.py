from __future__ import annotations

from flask import Flask, abort, g, render_template, request
from sqlalchemy import select
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from app.clock import Clock, SystemClock
from app.config import AppConfig
from app.csrf import csrf_token_is_valid, generate_csrf_token
from app.db import create_engine_for_app, get_session, init_db
from app.images import MAX_UPLOAD_BYTES, remove_stale_uploads
from app.logging import configure_logging
from app.models import WebSession
from app.rate_limit import MemoryRateLimitStore, RateLimiter, RateLimitStore
from app.security import SESSION_COOKIE_NAME, digest_session_token, security_headers
from app.times import naive_utc


def create_app(
    config: AppConfig | None = None,
    *,
    clock: Clock | None = None,
    rate_limit_store: RateLimitStore | None = None,
) -> Flask:
    config = config or AppConfig.from_environ()
    configure_logging(config.log_level)
    remove_stale_uploads(config.data_dir)

    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key
    app.config["APP_CONFIG"] = config
    # Leave multipart overhead room while image handling enforces the exact
    # 10 MB file limit before decoding.
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024 * 1024

    if config.trust_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    app_clock = clock or SystemClock()
    store = rate_limit_store or MemoryRateLimitStore()
    app.extensions["clock"] = app_clock
    app.extensions["rate_limiter"] = RateLimiter(store=store, clock=app_clock)

    engine = create_engine_for_app(config.database_url)
    init_db(app, engine)

    from app.views.admin import bp as admin_bp
    from app.views.customer import bp as customer_bp
    from app.views.health import bp as health_bp
    from app.views.pages import bp as pages_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(admin_bp)

    @app.before_request
    def load_web_session() -> None:
        g.web_session = None
        if request.path in {"/healthz", "/readyz"} or request.path.startswith(
            "/static/"
        ):
            return
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            return
        digest = digest_session_token(token)
        db_session = get_session()
        row = db_session.execute(
            select(WebSession).where(WebSession.session_digest == digest)
        ).scalar_one_or_none()
        if row is None:
            return
        now = naive_utc(app_clock.now())
        if row.expires_at <= now:
            db_session.delete(row)
            db_session.commit()
            return
        row.last_seen_at = now
        db_session.commit()
        g.web_session = row

    @app.before_request
    def enforce_csrf() -> None:
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        if request.path in {"/healthz", "/readyz"}:
            return
        token = request.form.get("csrf_token", "")
        digest = ""
        session = getattr(g, "web_session", None)
        if session is not None:
            digest = session.session_digest
        if not csrf_token_is_valid(config.secret_key, token, digest):
            abort(403)

    @app.context_processor
    def inject_template_globals() -> dict[str, object]:
        def csrf_token() -> str:
            digest = ""
            session = getattr(g, "web_session", None)
            if session is not None:
                digest = session.session_digest
            return generate_csrf_token(config.secret_key, digest)

        session = getattr(g, "web_session", None)
        return {
            "csrf_token": csrf_token,
            "admin_authenticated": (
                session is not None and session.actor_type == "admin"
            ),
            "customer_authenticated": (
                session is not None and session.actor_type == "booking"
            ),
        }

    @app.after_request
    def add_security_headers(response):
        return security_headers(response)

    @app.teardown_request
    def close_uploaded_files(_error: BaseException | None) -> None:
        # Werkzeug keeps multipart streams on the request. Closing them at the
        # request boundary releases spooled upload files on both normal and
        # validation-error paths.
        try:
            request.close()
        except RequestEntityTooLarge:
            # Parsing has already been rejected before a multipart stream was
            # created, so there is nothing to close.
            pass

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/forbidden.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/not_found.html"), 404

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return render_template("errors/request_too_large.html"), 413

    return app
