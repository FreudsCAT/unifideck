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
    """Launch ID filter."""
    def filter(self, record: logging.LogRecord) -> bool:
        """Filter."""
        record.launch_id = get_launch_id()
        return True
def install_launch_id_logging(
    root_logger: logging.Logger | None = None,
) -> None:
    """Install launch ID logging."""
    logger = root_logger or logging.getLogger()
    for existing in logger.filters:
        if isinstance(existing, LaunchIdFilter):
            return
    logger.addFilter(LaunchIdFilter())