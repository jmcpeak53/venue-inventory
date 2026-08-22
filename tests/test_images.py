from __future__ import annotations

import os

from app.images import STALE_UPLOAD_SECONDS, image_directory, remove_stale_uploads


def test_stale_temporary_uploads_are_reclaimed_without_touching_active_uploads(
    app_config,
) -> None:
    directory = image_directory(app_config.data_dir)
    directory.mkdir(parents=True)
    stale = directory / ".upload-stale.tmp"
    active = directory / ".upload-active.tmp"
    unrelated = directory / "keep.txt"
    stale.touch()
    active.touch()
    unrelated.touch()
    os.utime(stale, (0, 0))

    remove_stale_uploads(app_config.data_dir, now=STALE_UPLOAD_SECONDS + 1)

    assert not stale.exists()
    assert active.exists()
    assert unrelated.exists()
