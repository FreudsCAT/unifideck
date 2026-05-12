"""Time helpers for the subscription cache.

OP-22f | py_modules/unifideck/services/microsoft_subscription/time_utils.py

* ``_end_of_month_utc(now)`` — compute the next end-of-month UTC
  boundary, used as the default cache TTL for active subscriptions
  (Microsoft renews monthly);
* ``_fmt_ts(ts)`` — format a timestamp for human-readable display.
"""

from __future__ import annotations
from datetime import UTC, datetime


def _end_of_month_utc(now: datetime | None = None) -> float:
    """Return the POSIX timestamp of the next month's 1st in UTC.

    Used as the natural expiry for subscription cache entries:
    Xbox / Game Pass subscriptions renew monthly, so the tier may
    legitimately change at the month boundary. Rather than picking
    an arbitrary TTL (e.g. "24 hours"), we anchor on the actual
    renewal cadence.

    Args:
        now: optional reference timestamp. Defaults to
            ``datetime.now(UTC)`` — overridable for deterministic
            tests.

    Returns:
        POSIX timestamp of the first day of the following month
        at 00:00 UTC.
    """
    now = now if now is not None else datetime.now(UTC)
    if now.month == 12:
        nxt = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        nxt = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return nxt.timestamp()


def _fmt_ts(ts: float) -> str:
    """Format a POSIX timestamp as an ISO-8601 UTC string.

    Used in log lines so timestamps are unambiguous regardless of
    the user's locale or timezone.

    Args:
        ts: POSIX timestamp.

    Returns:
        ISO-8601 string with ``+00:00`` suffix.
    """
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()
