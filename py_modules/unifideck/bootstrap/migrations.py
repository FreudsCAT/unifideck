"""bootstrap.migrations — one-time startup migrations for on-disk state
left behind by older Unifideck versions.

Each migration is idempotent by construction: it only acts when the
legacy path exists AND the current path does not, so re-running it
after a successful migration (or on a fresh install with neither path
present) is always a no-op. There is deliberately no separate
"already migrated" flag/marker — existence of the current path IS the
marker, so migration state can never drift out of sync with the
migration's own precondition.

Add further entries to ``STARTUP_MIGRATIONS`` as more path/schema
drift between releases turns up. Every migration is synchronous,
best-effort (never raises — ``run_startup_migrations`` isolates each
one), and safe to call on every boot.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate_microsoft_token_filename(
    *,
    legacy_path: Path | None = None,
    current_path: Path | None = None,
) -> None:
    """Rename the legacy singular ``microsoft_token.json`` to the
    plural ``microsoft_tokens.json`` that the store actually reads.

    Versions before this fix wrote the Xbox/Microsoft OAuth token to
    ``~/.config/unifideck/microsoft_token.json`` (singular) via
    ``accounts.account_manager.auth_token_paths``, while
    ``stores/microsoft/microsoft_config.py`` and
    ``stores/microsoft/tokens/persistence.py`` have only ever read
    and written the plural ``microsoft_tokens.json``. The two never
    agreed, so a previously-signed-in user's token was silently
    invisible to the store after upgrading — no error, just
    "not logged in". (``persistence.py``'s own legacy-path fallback
    checks a third, unrelated location and never covered this one.)

    Args:
        legacy_path: Override for the singular pre-fix path (tests).
        current_path: Override for the plural path the store reads
            (tests).
    """
    legacy = legacy_path or Path(
        "~/.config/unifideck/microsoft_token.json",
    ).expanduser()
    current = current_path or Path(
        "~/.config/unifideck/microsoft_tokens.json",
    ).expanduser()

    if not legacy.is_file() or current.exists():
        return

    try:
        shutil.move(str(legacy), str(current))
        logger.info(
            "[Migrations] renamed legacy Microsoft token file %s -> %s",
            legacy, current,
        )
    except OSError as e:
        logger.warning(
            "[Migrations] failed to rename Microsoft token file "
            "%s -> %s: %s",
            legacy, current, e,
        )


STARTUP_MIGRATIONS: tuple[Callable[[], None], ...] = (
    migrate_microsoft_token_filename,
)


def run_startup_migrations() -> None:
    """Run every registered one-time migration, best-effort.

    Called once per boot from ``boot_plugin``, before stores are
    constructed and before config validation, so a migrated file is
    already in place the first time anything tries to read it. Each
    migration is isolated — one broken migration must never block
    plugin boot or stop the rest from running.
    """
    for migration in STARTUP_MIGRATIONS:
        try:
            migration()
        except Exception:
            logger.exception(
                "[Migrations] startup migration %s failed",
                getattr(migration, "__name__", migration),
            )
