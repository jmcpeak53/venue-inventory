from __future__ import annotations

import logging
import re
from datetime import timedelta
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import String, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_session
from app.images import (
    ImageValidationError,
    has_upload,
    normalize_upload,
    remove_normalized_image,
)
from app.models import InventoryItem, WebSession
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
MAX_ITEM_NAME_LENGTH = 200
MAX_ITEM_DESCRIPTION_LENGTH = 2_000
MAX_STOCK_QUANTITY = 2_147_483_647
QUANTITY_RE = re.compile(r"[0-9]+")


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


@bp.get("/items")
@admin_required
def item_list():
    query_text = request.args.get("q", "").strip()
    visibility = request.args.get("visibility", "all")
    if visibility not in {"all", "visible", "hidden"}:
        visibility = "all"

    statement = select(InventoryItem)
    if query_text:
        escaped = (
            query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped.lower()}%"
        statement = statement.where(
            or_(
                InventoryItem.name.ilike(pattern, escape="\\"),
                InventoryItem.description.cast(String).ilike(pattern, escape="\\"),
            )
        )
    if visibility == "visible":
        statement = statement.where(InventoryItem.is_visible.is_(True))
    elif visibility == "hidden":
        statement = statement.where(InventoryItem.is_visible.is_(False))

    items = (
        get_session()
        .execute(
            statement.order_by(InventoryItem.updated_at.desc(), InventoryItem.id.desc())
        )
        .scalars()
        .all()
    )
    return render_template(
        "admin/item_list.html",
        items=items,
        query_text=query_text,
        visibility=visibility,
    )


@bp.get("/items/new")
@admin_required
def item_new():
    return _render_item_form(values=_new_item_values())


@bp.post("/items")
@admin_required
def item_create():
    values, errors = _item_values_from_request()
    if errors:
        return _render_item_form(values=values, errors=errors)

    image_filename, image_error = _save_submitted_image()
    if image_error:
        errors["image"] = image_error
        return _render_item_form(values=values, errors=errors)

    now = naive_utc(current_app.extensions["clock"].now())
    item = InventoryItem(
        name=values["name"],
        description=values["description"],
        stock_quantity=values["stock_quantity"],
        image_filename=image_filename,
        is_visible=values["is_visible"],
        created_at=now,
        updated_at=now,
    )
    db_session = get_session()
    db_session.add(item)
    try:
        db_session.commit()
    except SQLAlchemyError:
        db_session.rollback()
        _cleanup_image_after_commit(image_filename)
        logger.exception("Creating an inventory item could not be committed.")
        errors["form"] = "The item could not be saved. Try again."
        return _render_item_form(values=values, errors=errors)

    return redirect(url_for("admin.item_detail", item_id=item.id))


@bp.get("/items/<int:item_id>")
@admin_required
def item_detail(item_id: int):
    return render_template("admin/item_detail.html", item=_item_or_404(item_id))


@bp.get("/items/<int:item_id>/edit")
@admin_required
def item_edit(item_id: int):
    item = _item_or_404(item_id)
    return _render_item_form(item=item, values=_item_values_from_item(item))


@bp.post("/items/<int:item_id>")
@admin_required
def item_update(item_id: int):
    item = _item_or_404(item_id)
    values, errors = _item_values_from_request()
    if errors:
        return _render_item_form(item=item, values=values, errors=errors)

    image_filename, image_error = _save_submitted_image()
    if image_error:
        errors["image"] = image_error
        return _render_item_form(item=item, values=values, errors=errors)

    prior_image_filename = item.image_filename
    if image_filename is not None:
        item.image_filename = image_filename
    elif request.form.get("remove_image") == "1":
        item.image_filename = None
    item.name = values["name"]
    item.description = values["description"]
    item.stock_quantity = values["stock_quantity"]
    item.is_visible = values["is_visible"]
    item.updated_at = naive_utc(current_app.extensions["clock"].now())

    db_session = get_session()
    try:
        db_session.commit()
    except SQLAlchemyError:
        db_session.rollback()
        _cleanup_image_after_commit(image_filename)
        logger.exception("Updating inventory item %s could not be committed.", item_id)
        errors["form"] = "The item could not be saved. Try again."
        return _render_item_form(item=item, values=values, errors=errors)

    if prior_image_filename and prior_image_filename != item.image_filename:
        _cleanup_image_after_commit(prior_image_filename)
    return redirect(url_for("admin.item_detail", item_id=item.id))


@bp.post("/items/<int:item_id>/visibility")
@admin_required
def item_toggle_visibility(item_id: int):
    item = _item_or_404(item_id)
    item.is_visible = not item.is_visible
    item.updated_at = naive_utc(current_app.extensions["clock"].now())
    db_session = get_session()
    try:
        db_session.commit()
    except SQLAlchemyError:
        db_session.rollback()
        logger.exception(
            "Changing inventory item %s visibility could not commit.", item_id
        )
        abort(500)
    return redirect(url_for("admin.item_detail", item_id=item.id))


@bp.get("/items/<int:item_id>/delete")
@admin_required
def item_delete_confirm(item_id: int):
    return render_template("admin/item_delete.html", item=_item_or_404(item_id))


@bp.post("/items/<int:item_id>/delete")
@admin_required
def item_delete(item_id: int):
    item = _item_or_404(item_id)
    image_filename = item.image_filename
    db_session = get_session()
    db_session.delete(item)
    try:
        db_session.commit()
    except SQLAlchemyError:
        db_session.rollback()
        logger.exception("Deleting inventory item %s could not be committed.", item_id)
        return render_template(
            "admin/item_delete.html",
            item=item,
            error="The item could not be deleted. Try again.",
        )

    if image_filename:
        _cleanup_image_after_commit(image_filename)
    return redirect(url_for("admin.item_list"))


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


def _item_or_404(item_id: int) -> InventoryItem:
    item = get_session().get(InventoryItem, item_id)
    if item is None:
        abort(404)
    return item


def _render_item_form(
    *,
    values: dict[str, object],
    item: InventoryItem | None = None,
    errors: dict[str, str] | None = None,
):
    return render_template(
        "admin/item_form.html", item=item, values=values, errors=errors or {}
    )


def _new_item_values() -> dict[str, object]:
    return {
        "name": "",
        "description": "",
        "stock_quantity": "",
        "is_visible": True,
    }


def _item_values_from_item(item: InventoryItem) -> dict[str, object]:
    return {
        "name": item.name,
        "description": item.description or "",
        "stock_quantity": str(item.stock_quantity),
        "is_visible": item.is_visible,
    }


def _item_values_from_request() -> tuple[dict[str, object], dict[str, str]]:
    name = request.form.get("name", "").strip()
    description_raw = request.form.get("description", "")
    description = description_raw.strip()
    quantity_raw = request.form.get("stock_quantity", "").strip()
    values: dict[str, object] = {
        "name": name,
        "description": description_raw,
        "stock_quantity": quantity_raw,
        "is_visible": request.form.get("is_visible") == "1",
    }
    errors: dict[str, str] = {}

    if not name:
        errors["name"] = "Enter an item name."
    elif len(name) > MAX_ITEM_NAME_LENGTH:
        errors["name"] = (
            f"Item names must be {MAX_ITEM_NAME_LENGTH} characters or fewer."
        )

    if len(description) > MAX_ITEM_DESCRIPTION_LENGTH:
        errors["description"] = (
            f"Descriptions must be {MAX_ITEM_DESCRIPTION_LENGTH} characters or fewer."
        )
    elif any(
        ord(character) < 32 and character not in "\n\r\t" for character in description
    ):
        errors["description"] = "Descriptions must contain plain text."

    if not quantity_raw:
        errors["stock_quantity"] = "Enter a stock quantity."
    elif QUANTITY_RE.fullmatch(quantity_raw) is None:
        errors["stock_quantity"] = "Stock quantity must be a nonnegative whole number."
    else:
        quantity = int(quantity_raw)
        if quantity > MAX_STOCK_QUANTITY:
            errors["stock_quantity"] = "Stock quantity is too large."
        else:
            values["stock_quantity"] = quantity

    values["description"] = description or None
    return values, errors


def _save_submitted_image() -> tuple[str | None, str | None]:
    upload = request.files.get("image")
    if upload is None:
        return None, None
    try:
        if not has_upload(upload):
            return None, None
        try:
            return (
                normalize_upload(upload, current_app.config["APP_CONFIG"].data_dir),
                None,
            )
        except ImageValidationError as exc:
            return None, str(exc)
    finally:
        upload.close()


def _cleanup_image_after_commit(filename: str | None) -> None:
    if filename is None:
        return
    if not remove_normalized_image(current_app.config["APP_CONFIG"].data_dir, filename):
        logger.warning(
            "Could not remove normalized inventory image after database commit.",
            extra={"event": "inventory_image_cleanup_failed", "filename": filename},
        )
