from datetime import UTC, date, datetime


def now() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return now().date()


def as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; everything we store is UTC, so re-attach the zone."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
