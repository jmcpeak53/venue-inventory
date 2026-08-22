from __future__ import annotations

import os
import tempfile
import uuid
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SOURCE_PIXELS = 20_000_000
MAX_IMAGE_DIMENSION = 1600
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
IMAGE_FILENAME_SUFFIX = ".webp"


class ImageValidationError(ValueError):
    """The supplied upload cannot safely become a catalog image."""


def image_directory(data_dir: Path) -> Path:
    return data_dir / "images"


def has_upload(upload: FileStorage | None) -> bool:
    return upload is not None and bool(upload.filename)


def normalize_upload(upload: FileStorage, data_dir: Path) -> str:
    """Decode an upload and atomically place a metadata-free WebP image.

    The returned filename is intentionally generated here rather than derived
    from the client's filename. The caller owns deleting it if its following
    database transaction cannot commit.
    """

    encoded = _read_upload(upload)
    normalized = _decode_and_normalize(encoded)
    directory = image_directory(data_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ImageValidationError("The image could not be stored. Try again.") from exc

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", dir=directory, prefix=".upload-", suffix=".tmp", delete=False
        ) as output:
            temporary_path = Path(output.name)
            normalized.save(
                output,
                format="WEBP",
                quality=85,
                method=6,
                exif=b"",
                icc_profile=None,
            )
            output.flush()
            os.fsync(output.fileno())

        for _ in range(10):
            filename = f"{uuid.uuid4().hex}{IMAGE_FILENAME_SUFFIX}"
            destination = directory / filename
            try:
                # link() creates the final name only when it does not already
                # exist, without exposing a partially encoded image.
                os.link(temporary_path, destination)
            except FileExistsError:
                continue
            os.unlink(temporary_path)
            temporary_path = None
            return filename
        raise OSError("Could not allocate a unique image filename.")
    except OSError as exc:
        raise ImageValidationError("The image could not be stored. Try again.") from exc
    finally:
        normalized.close()
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def remove_normalized_image(data_dir: Path, filename: str) -> bool:
    """Remove a generated image after a committed database change.

    A false return leaves a harmless orphan for later operational cleanup; it
    never changes a record that has already committed.
    """

    if not is_generated_image_filename(filename):
        return False
    try:
        (image_directory(data_dir) / filename).unlink(missing_ok=True)
    except OSError:
        return False
    return True


def is_generated_image_filename(filename: str) -> bool:
    stem, suffix = os.path.splitext(filename)
    if suffix != IMAGE_FILENAME_SUFFIX or len(stem) != 32:
        return False
    try:
        int(stem, 16)
    except ValueError:
        return False
    return stem == stem.lower()


def _read_upload(upload: FileStorage) -> bytes:
    try:
        encoded = upload.stream.read(MAX_UPLOAD_BYTES + 1)
    except OSError as exc:
        raise ImageValidationError("The uploaded image could not be read.") from exc
    if not encoded:
        raise ImageValidationError("Choose a JPEG, PNG, or WebP image to upload.")
    if len(encoded) > MAX_UPLOAD_BYTES:
        raise ImageValidationError("Images must be 10 MB or smaller.")
    return encoded


def _decode_and_normalize(encoded: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(encoded)) as verification_image:
                if verification_image.format not in ALLOWED_FORMATS:
                    raise ImageValidationError("Use a JPEG, PNG, or WebP image.")
                verification_image.verify()

            with Image.open(BytesIO(encoded)) as decoded:
                if decoded.format not in ALLOWED_FORMATS:
                    raise ImageValidationError("Use a JPEG, PNG, or WebP image.")
                if getattr(decoded, "n_frames", 1) != 1:
                    raise ImageValidationError("Animated images are not supported.")
                width, height = decoded.size
                if width * height > MAX_SOURCE_PIXELS:
                    raise ImageValidationError("The image dimensions are too large.")
                decoded.load()
                oriented = ImageOps.exif_transpose(decoded)
                try:
                    return _webp_ready_copy(oriented)
                finally:
                    if oriented is not decoded:
                        oriented.close()
    except ImageValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        raise ImageValidationError(
            "The uploaded file is not a valid, safe JPEG, PNG, or WebP image."
        ) from exc


def _webp_ready_copy(image: Image.Image) -> Image.Image:
    has_alpha = image.mode in {"LA", "RGBA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    mode = "RGBA" if has_alpha else "RGB"
    prepared = image.convert(mode)
    prepared.thumbnail(
        (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS
    )
    return prepared
