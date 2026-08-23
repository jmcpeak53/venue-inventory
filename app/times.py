from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


CHICAGO_TIME_ZONE = ZoneInfo("America/Chicago")


def as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def naive_utc(moment: datetime) -> datetime:
    return as_utc(moment).replace(tzinfo=None)


def chicago_date(moment: datetime) -> date:
    """Return the Chicago calendar date for an injected clock instant."""

    return as_utc(moment).astimezone(CHICAGO_TIME_ZONE).date()
