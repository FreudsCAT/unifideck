"""Startup migrations must be one-time and never clobber good state.

Covers the Microsoft token filename drift found while testing the
0.6.1 -> 0.7.1 upgrade path: older versions wrote the Xbox/Microsoft
OAuth token to a singular ``microsoft_token.json`` while the store's
own persistence layer only ever reads the plural
``microsoft_tokens.json``, so a previously-signed-in user's token
went silently unread after upgrading.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from unifideck.bootstrap import migrations


def test_migrates_legacy_file_when_current_absent(tmp_path: Path) -> None:
    legacy = tmp_path / "microsoft_token.json"
    current = tmp_path / "microsoft_tokens.json"
    legacy.write_text('{"access_token": "abc"}')

    migrations.migrate_microsoft_token_filename(
        legacy_path=legacy, current_path=current,
    )

    assert not legacy.exists()
    assert current.read_text() == '{"access_token": "abc"}'


def test_noop_when_legacy_absent(tmp_path: Path) -> None:
    legacy = tmp_path / "microsoft_token.json"
    current = tmp_path / "microsoft_tokens.json"

    migrations.migrate_microsoft_token_filename(
        legacy_path=legacy, current_path=current,
    )

    assert not legacy.exists()
    assert not current.exists()


def test_noop_and_no_clobber_when_both_exist(tmp_path: Path) -> None:
    """The current file always wins — never overwritten by a stale legacy copy."""
    legacy = tmp_path / "microsoft_token.json"
    current = tmp_path / "microsoft_tokens.json"
    legacy.write_text("old-token")
    current.write_text("current-token")

    migrations.migrate_microsoft_token_filename(
        legacy_path=legacy, current_path=current,
    )

    assert legacy.read_text() == "old-token"
    assert current.read_text() == "current-token"


def test_second_run_after_migration_is_a_noop(tmp_path: Path) -> None:
    """Re-running after a successful migration must not error or move anything."""
    legacy = tmp_path / "microsoft_token.json"
    current = tmp_path / "microsoft_tokens.json"
    legacy.write_text('{"access_token": "abc"}')

    migrations.migrate_microsoft_token_filename(
        legacy_path=legacy, current_path=current,
    )
    migrations.migrate_microsoft_token_filename(
        legacy_path=legacy, current_path=current,
    )

    assert not legacy.exists()
    assert current.read_text() == '{"access_token": "abc"}'


def test_run_startup_migrations_isolates_a_failing_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One broken migration must not stop the others or raise out of boot."""
    good = MagicMock()
    bad = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(migrations, "STARTUP_MIGRATIONS", (bad, good))

    migrations.run_startup_migrations()

    bad.assert_called_once()
    good.assert_called_once()
