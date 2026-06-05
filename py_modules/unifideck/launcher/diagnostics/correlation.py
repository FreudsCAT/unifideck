from __future__ import annotations

import contextvars
import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager

_LAUNCH_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "unifideck_launch_id",
    default="-",
)
def new_launch_id() -> str:
    """New launch ID."""
    return secrets.token_hex(4)
def get_launch_id() -> str:
    """Get launch ID."""
    return _LAUNCH_ID.get()
@contextmanager
def launch_id_scope(launch_id: str) -> Iterator[None]:
    """Launch ID scope."""
    token = _LAUNCH_ID.set(launch_id)
    try:
        yield
    finally:
        _LAUNCH_ID.reset(token)
class LaunchIdFilter(logging.Filter):
    """Launch ID filter (legacy — kept for callers/tests)."""
    def filter(self, record: logging.LogRecord) -> bool:
        """Filter."""
        record.launch_id = get_launch_id()
        return True
def install_launch_id_logging() -> None:
    """Stamp ``launch_id`` onto every log record via a record factory.

    A logger-level filter (the previous approach) only runs for records
    logged *directly* to that logger — records propagating up from child
    loggers (``unifideck.services.launcher.*`` etc.) reach the root's
    stderr handler, whose formatter uses ``%(launch_id)s``, WITHOUT the
    attribute → ``ValueError: Formatting field not found: 'launch_id'``
    on every such record. A ``LogRecordFactory`` runs for ALL records
    regardless of origin, so the field is always present. Idempotent.
    """
    factory = logging.getLogRecordFactory()
    if getattr(factory, "_unifideck_launch_id", False):
        return

    def _factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        if not hasattr(record, "launch_id"):
            record.launch_id = get_launch_id()
        return record

    _factory._unifideck_launch_id = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_factory)
