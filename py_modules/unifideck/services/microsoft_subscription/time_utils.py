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
    """End of month utc."""
    now = now if now is not None else datetime.now(UTC)
    if now.month == 12:
        nxt = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        nxt = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return nxt.timestamp()


def _fmt_ts(ts: float) -> str:
    """Fmt ts."""
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()
