from __future__ import annotations

from datetime import UTC, datetime


def as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def naive_utc(moment: datetime) -> datetime:
    return as_utc(moment).replace(tzinfo=None)
