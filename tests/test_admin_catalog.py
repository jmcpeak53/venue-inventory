from __future__ import annotations

from io import BytesIO

import pytest
from app.db import get_session
from app.images import MAX_UPLOAD_BYTES, image_directory
from app.models import InventoryItem
from flask.testing import FlaskClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from tests.conftest import csrf_token, sign_in


def test_catalog_requires_an_administrator(client: FlaskClient) -> None:
    response = client.get("/admin/items")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_catalog_writes_require_csrf(app, client: FlaskClient) -> None:
    assert sign_in(client).status_code == 302
    response = client.post(
        "/admin/items", data={"name": "Chair", "stock_quantity": "1"}
    )
    assert response.status_code == 403
    with app.app_context():
        assert get_session().execute(select(InventoryItem)).scalars().all() == []


def test_create_item_without_an_image_shows_a_placeholder(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    response = submit_item(
        client, "/admin/items", name="Brass candlesticks", quantity="12"
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/items/1")
    detail = client.get(response.headers["Location"])
    body = detail.get_data(as_text=True)
    assert "Brass candlesticks" in body
    assert 'aria-label="No image available"' in body

    with app.app_context():
        item = get_session().get(InventoryItem, 1)
        assert item is not None
        assert item.description is None
        assert item.stock_quantity == 12
        assert item.is_visible is True
        assert item.image_filename is None


@pytest.mark.parametrize("quantity", ["", "-1", "1.5", "twelve"])
def test_invalid_quantities_show_an_accessible_error(
    app, client: FlaskClient, quantity: str
) -> None:
    assert sign_in(client).status_code == 302
    response = submit_item(client, "/admin/items", name="Chair", quantity=quantity)

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Stock quantity" in body
    assert 'aria-invalid="true"' in body
    with app.app_context():
        assert get_session().execute(select(InventoryItem)).scalars().all() == []


def test_list_search_and_visibility_filter(client: FlaskClient) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(
            client,
            "/admin/items",
            name="Velvet chair",
            quantity="3",
            description="A deep blue seating option",
        ).status_code
        == 302
    )
    assert (
        submit_item(
            client,
            "/admin/items",
            name="Gold vase",
            quantity="4",
            visible=False,
        ).status_code
        == 302
    )

    by_name = client.get("/admin/items?q=velvet")
    assert "Velvet chair" in by_name.get_data(as_text=True)
    assert "Gold vase" not in by_name.get_data(as_text=True)

    by_description = client.get("/admin/items?q=blue")
    assert "Velvet chair" in by_description.get_data(as_text=True)

    visible = client.get("/admin/items?visibility=visible")
    assert "Velvet chair" in visible.get_data(as_text=True)
    assert "Gold vase" not in visible.get_data(as_text=True)

    hidden = client.get("/admin/items?visibility=hidden")
    assert "Gold vase" in hidden.get_data(as_text=True)
    assert "Velvet chair" not in hidden.get_data(as_text=True)


def test_edit_visibility_toggle_and_confirmed_delete(app, client: FlaskClient) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Table", quantity="6").status_code
        == 302
    )

    edit = client.get("/admin/items/1/edit")
    update = client.post(
        "/admin/items/1",
        data={
            "csrf_token": csrf_token(edit),
            "name": "Round table",
            "description": "Seats eight",
            "stock_quantity": "8",
        },
    )
    assert update.status_code == 302
    detail = client.get("/admin/items/1")
    assert "Round table" in detail.get_data(as_text=True)
    assert "Seats eight" in detail.get_data(as_text=True)
    assert "Hidden from the catalog" in detail.get_data(as_text=True)

    show = client.post(
        "/admin/items/1/visibility",
        data={"csrf_token": csrf_token(detail)},
    )
    assert show.status_code == 302
    assert "Shown in the catalog" in client.get("/admin/items/1").get_data(as_text=True)

    confirmation = client.get("/admin/items/1/delete")
    assert "Delete item?" in confirmation.get_data(as_text=True)
    deleted = client.post(
        "/admin/items/1/delete",
        data={"csrf_token": csrf_token(confirmation)},
    )
    assert deleted.status_code == 302
    with app.app_context():
        assert get_session().get(InventoryItem, 1) is None


def test_image_normalization_orientation_and_public_route(
    app, app_config, client
) -> None:
    assert sign_in(client).status_code == 302
    source = image_bytes(
        "JPEG", size=(2_400, 1_200), orientation=6, exif_comment="secret"
    )
    create = submit_item(
        client,
        "/admin/items",
        name="Backdrop",
        quantity="1",
        image=(source, "phone-photo.jpg", "image/jpeg"),
    )
    assert create.status_code == 302

    with app.app_context():
        item = get_session().get(InventoryItem, 1)
        assert item is not None
        filename = item.image_filename
        assert filename is not None
        assert filename.endswith(".webp")
        assert (image_directory(app_config.data_dir) / filename).is_file()

    image_response = client.get(f"/images/{filename}", buffered=True)
    assert image_response.status_code == 200
    assert image_response.mimetype == "image/webp"
    with Image.open(BytesIO(image_response.data)) as normalized:
        assert normalized.format == "WEBP"
        assert normalized.size == (800, 1600)
        assert "exif" not in normalized.info
        assert "icc_profile" not in normalized.info

    anonymous = app.test_client()
    assert anonymous.get(f"/images/{filename}", buffered=True).status_code == 200
    assert (
        anonymous.get(
            "/images/../../venue-inventory.sqlite3", buffered=True
        ).status_code
        == 404
    )
    assert anonymous.get("/images/not-an-image.webp", buffered=True).status_code == 404


def test_replacing_removing_and_deleting_images_happens_after_the_record_commit(
    app, app_config, client
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(
            client,
            "/admin/items",
            name="Lantern",
            quantity="2",
            image=(image_bytes("PNG"), "lantern.png", "image/png"),
        ).status_code
        == 302
    )
    with app.app_context():
        first_name = get_session().get(InventoryItem, 1).image_filename
    assert first_name is not None
    first_path = image_directory(app_config.data_dir) / first_name
    assert first_path.is_file()

    edit = client.get("/admin/items/1/edit")
    replace = client.post(
        "/admin/items/1",
        data={
            "csrf_token": csrf_token(edit),
            "name": "Lantern",
            "description": "",
            "stock_quantity": "2",
            "is_visible": "1",
            "image": (
                image_bytes("WEBP", color=(20, 30, 40)),
                "new.webp",
                "image/webp",
            ),
        },
        content_type="multipart/form-data",
    )
    assert replace.status_code == 302
    with app.app_context():
        second_name = get_session().get(InventoryItem, 1).image_filename
    assert second_name is not None and second_name != first_name
    second_path = image_directory(app_config.data_dir) / second_name
    assert second_path.is_file()
    assert not first_path.exists()

    edit = client.get("/admin/items/1/edit")
    remove = client.post(
        "/admin/items/1",
        data={
            "csrf_token": csrf_token(edit),
            "name": "Lantern",
            "description": "",
            "stock_quantity": "2",
            "is_visible": "1",
            "remove_image": "1",
        },
    )
    assert remove.status_code == 302
    assert not second_path.exists()

    edit = client.get("/admin/items/1/edit")
    add_again = client.post(
        "/admin/items/1",
        data={
            "csrf_token": csrf_token(edit),
            "name": "Lantern",
            "description": "",
            "stock_quantity": "2",
            "is_visible": "1",
            "image": (image_bytes("PNG"), "last.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert add_again.status_code == 302
    with app.app_context():
        final_name = get_session().get(InventoryItem, 1).image_filename
    assert final_name is not None
    final_path = image_directory(app_config.data_dir) / final_name

    confirmation = client.get("/admin/items/1/delete")
    assert (
        client.post(
            "/admin/items/1/delete", data={"csrf_token": csrf_token(confirmation)}
        ).status_code
        == 302
    )
    assert not final_path.exists()


@pytest.mark.parametrize(
    "payload, filename, expected_message",
    [
        (b"not an image", "fake.png", "not a valid"),
        (b"\x89PNG\r\n\x1a\n", "truncated.png", "not a valid"),
    ],
)
def test_invalid_uploads_do_not_change_an_existing_image(
    app, app_config, client, payload: bytes, filename: str, expected_message: str
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(
            client,
            "/admin/items",
            name="Arch",
            quantity="1",
            image=(image_bytes("PNG"), "before.png", "image/png"),
        ).status_code
        == 302
    )
    with app.app_context():
        before_name = get_session().get(InventoryItem, 1).image_filename
    assert before_name is not None
    before_path = image_directory(app_config.data_dir) / before_name
    before_bytes = before_path.read_bytes()

    edit = client.get("/admin/items/1/edit")
    response = client.post(
        "/admin/items/1",
        data={
            "csrf_token": csrf_token(edit),
            "name": "Arch",
            "description": "",
            "stock_quantity": "1",
            "is_visible": "1",
            "image": (BytesIO(payload), filename, "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert expected_message in response.get_data(as_text=True)
    with app.app_context():
        assert get_session().get(InventoryItem, 1).image_filename == before_name
    assert before_path.read_bytes() == before_bytes


def test_oversized_upload_is_rejected_before_creating_an_item(
    app, client, monkeypatch
) -> None:
    assert sign_in(client).status_code == 302
    # Lower the unit-test boundary so this HTTP test exercises the same stream
    # limit without forcing Werkzeug to spill an 11 MB fixture to disk.
    monkeypatch.setattr("app.images.MAX_UPLOAD_BYTES", 10)
    response = submit_item(
        client,
        "/admin/items",
        name="Oversized",
        quantity="1",
        image=(BytesIO(b"x" * 11), "oversize.png", "image/png"),
    )
    assert response.status_code == 200
    assert "10 MB or smaller" in response.get_data(as_text=True)
    with app.app_context():
        assert get_session().execute(select(InventoryItem)).scalars().all() == []


def test_request_over_the_server_limit_has_a_safe_response(client: FlaskClient) -> None:
    assert sign_in(client).status_code == 302
    form = client.get("/admin/items/new")
    response = client.post(
        "/admin/items",
        data={"csrf_token": csrf_token(form), "name": "Ignored", "stock_quantity": "1"},
        environ_overrides={"CONTENT_LENGTH": str(MAX_UPLOAD_BYTES + 1024 * 1024 + 1)},
    )
    assert response.status_code == 413
    assert "10 MB or smaller" in response.get_data(as_text=True)


def test_database_failure_removes_a_new_upload_and_keeps_the_existing_record(
    app, app_config, client, monkeypatch
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(
            client,
            "/admin/items",
            name="Existing",
            quantity="1",
            image=(image_bytes("PNG"), "before.png", "image/png"),
        ).status_code
        == 302
    )
    with app.app_context():
        before_name = get_session().get(InventoryItem, 1).image_filename
    assert before_name is not None
    before_path = image_directory(app_config.data_dir) / before_name

    def fail_inventory_commit(session: Session) -> None:
        if session.dirty and any(
            isinstance(row, InventoryItem) for row in session.dirty
        ):
            raise SQLAlchemyError("forced database failure")

    from sqlalchemy import event

    event.listen(Session, "before_commit", fail_inventory_commit)
    try:
        edit = client.get("/admin/items/1/edit")
        response = client.post(
            "/admin/items/1",
            data={
                "csrf_token": csrf_token(edit),
                "name": "Existing updated",
                "description": "",
                "stock_quantity": "1",
                "is_visible": "1",
                "image": (
                    image_bytes("PNG", color=(99, 20, 30)),
                    "after.png",
                    "image/png",
                ),
            },
            content_type="multipart/form-data",
        )
    finally:
        event.remove(Session, "before_commit", fail_inventory_commit)

    assert response.status_code == 200
    assert "could not be saved" in response.get_data(as_text=True)
    with app.app_context():
        item = get_session().get(InventoryItem, 1)
        assert item is not None
        assert item.name == "Existing"
        assert item.image_filename == before_name
    assert before_path.is_file()
    assert list(image_directory(app_config.data_dir).glob("*.webp")) == [before_path]


def test_storage_failure_keeps_the_existing_image_usable(
    app, app_config, client, monkeypatch
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(
            client,
            "/admin/items",
            name="Existing",
            quantity="1",
            image=(image_bytes("PNG"), "before.png", "image/png"),
        ).status_code
        == 302
    )
    with app.app_context():
        before_name = get_session().get(InventoryItem, 1).image_filename
    assert before_name is not None
    before_path = image_directory(app_config.data_dir) / before_name

    def cannot_place_image(_source, _destination) -> None:
        raise OSError("read-only directory")

    monkeypatch.setattr("app.images.os.link", cannot_place_image)
    edit = client.get("/admin/items/1/edit")
    response = client.post(
        "/admin/items/1",
        data={
            "csrf_token": csrf_token(edit),
            "name": "Existing updated",
            "description": "",
            "stock_quantity": "1",
            "is_visible": "1",
            "image": (image_bytes("PNG", color=(99, 20, 30)), "after.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert "could not be stored" in response.get_data(as_text=True)
    with app.app_context():
        item = get_session().get(InventoryItem, 1)
        assert item is not None
        assert item.name == "Existing"
        assert item.image_filename == before_name
    assert before_path.is_file()
    assert list(image_directory(app_config.data_dir).glob("*.webp")) == [before_path]


def test_dangerous_dimensions_are_rejected_before_item_creation(
    app, client, monkeypatch
) -> None:
    assert sign_in(client).status_code == 302
    monkeypatch.setattr("app.images.MAX_SOURCE_PIXELS", 1)

    response = submit_item(
        client,
        "/admin/items",
        name="Too large",
        quantity="1",
        image=(image_bytes("PNG", size=(2, 2)), "large.png", "image/png"),
    )
    assert response.status_code == 200
    assert "dimensions are too large" in response.get_data(as_text=True)
    with app.app_context():
        assert get_session().execute(select(InventoryItem)).scalars().all() == []


def submit_item(
    client: FlaskClient,
    path: str,
    *,
    name: str,
    quantity: str,
    description: str = "",
    visible: bool = True,
    image: tuple[BytesIO, str, str] | None = None,
):
    form = client.get("/admin/items/new")
    data: dict[str, object] = {
        "csrf_token": csrf_token(form),
        "name": name,
        "description": description,
        "stock_quantity": quantity,
    }
    if visible:
        data["is_visible"] = "1"
    if image:
        data["image"] = image
        return client.post(path, data=data, content_type="multipart/form-data")
    return client.post(path, data=data)


def image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (30, 20),
    color: tuple[int, int, int] = (120, 80, 40),
    orientation: int | None = None,
    exif_comment: str | None = None,
) -> BytesIO:
    image = Image.new("RGB", size, color)
    output = BytesIO()
    save_kwargs: dict[str, object] = {}
    if orientation is not None or exif_comment is not None:
        exif = Image.Exif()
        if orientation is not None:
            exif[274] = orientation
        if exif_comment is not None:
            exif[37510] = exif_comment.encode()
        save_kwargs["exif"] = exif.tobytes()
    image.save(output, format=image_format, **save_kwargs)
    image.close()
    output.seek(0)
    return output
