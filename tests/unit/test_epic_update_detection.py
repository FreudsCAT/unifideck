"""Epic update detection: parse the scan, and update in place.

Reported as "Rocket League has an update but the button still says Play".
The backend was never the problem — captured live on the dev Deck,
``legendary list-installed --check-updates`` exits 0 and prints::

     * Rocket League® (App name: Sugar | Version: ++Prime+Update59-CL-520762 …)
      -> Update available! Installed: ++Prime+Update59-CL-520762,
         Latest: ++Prime+Update59.1-CL-523543

The bug was in the frontend, which read ``has_update`` off the RPC
envelope instead of out of its ``data``. These tests pin the two backend
halves that fix reached, so a future refactor can't quietly break them:

1. ``_parse_update_output`` against the REAL 13-game scan from that
   device — including the ``!`` "directory missing" lines that legendary
   prints *instead of* an update line and that must never be mistaken
   for one;
2. ``update_game`` resolving its base path from the top-level
   ``install_path`` key legendary actually writes. It used to read a
   nested ``install.install_path`` that does not exist in
   ``installed.json``, so the path was always ``None``.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.core.types import InstallResult
from unifideck.stores.epic.updates import EpicUpdateChecker

# Verbatim stdout of ``legendary list-installed`` on the dev Deck,
# 2026-08-06. Only Rocket League has a pending update; eight entries are
# stale records whose directories are gone (an unmounted/re-imaged SD
# card), and those print a ``!`` line where an update line would go.
_REAL_SCAN = """
Installed games:
 * 20 Minutes Till Dawn (App name: 4656facc740742a39e265b026e13d075 | Version: 1.0-win | Platform: Windows | 0.12 GiB)
  ! Game does no longer appear to be installed (directory "/run/media/deck/microSTEAMDECK/Games/MinutesTillDawnxgosd" missing)!
 * AK-xolotl: Together (App name: d578a8f36f8c49dc8c952c30f7dd049f | Version: 2.5.30 | Platform: Windows | 0.60 GiB)
  ! Game does no longer appear to be installed (directory "/run/media/deck/microSTEAMDECK/Games/AKxolotlokDYC" missing)!
 * Arranger: A Role-Puzzling Adventure (App name: d83a370c98f1474ca80de267ef9f54ed | Version: 1.1.18.pc-3 | Platform: Windows | 7.84 GiB)
  ! Game does no longer appear to be installed (directory "/run/media/deck/microSTEAMDECK/Games/Arranger" missing)!
 * Bloodstained: Ritual of the Night (App name: a2ac59c83b704e40b4ab3a9e963fef52 | Version: r1595_67193 | Platform: Windows | 10.99 GiB)
  ! Game does no longer appear to be installed (directory "/run/media/deck/microSTEAMDECK/Games/BloodstainedftBhx" missing)!
 * River City Girls 2 (App name: 972f47c7ed8e4eeaae92cb6b8d5b3fd7 | Version: v.1.0.903406_patched_v2 | Platform: Windows | 6.45 GiB)
 * Rocket League® (App name: Sugar | Version: ++Prime+Update59-CL-520762 | Platform: Windows | 39.53 GiB)
  -> Update available! Installed: ++Prime+Update59-CL-520762, Latest: ++Prime+Update59.1-CL-523543
 * Weird West: Definitive Edition (App name: 5ab7c9b39f81481c88dce1e4f106a594 | Version: 0.0.78819.118 | Platform: Windows | 11.85 GiB)
  ! Game does no longer appear to be installed (directory "/run/media/deck/microSTEAMDECK/Games/WeirdWest" missing)!

Total: 7
"""  # noqa: E501 — verbatim CLI output; wrapping it would stop it being a fixture


def _checker() -> EpicUpdateChecker:
    """A checker with no real CLI behind it — call sites are patched."""
    return EpicUpdateChecker(
        bus=None,  # type: ignore[arg-type]  # unused on these paths
        cli_path="/nonexistent/legendary",
        library=None,  # type: ignore[arg-type]  # replaced per-test
        list_updates_timeout=60,
        size_cache_ttl=3600,
        info_timeout=30.0,
    )


def test_parses_only_the_game_with_a_pending_update() -> None:
    """The real scan yields exactly Rocket League's app name."""
    assert EpicUpdateChecker._parse_update_output(_REAL_SCAN) == ["Sugar"]


def test_directory_missing_is_not_an_update() -> None:
    """legendary prints ``!`` *instead of* the update line — never both.

    Treating a stale record as updatable would offer an Update button for
    a game whose files are gone, and the "update" would silently become a
    full re-download.
    """
    assert EpicUpdateChecker._parse_update_output(_REAL_SCAN) != []
    for missing in ("4656facc740742a39e265b026e13d075", "WeirdWest"):
        assert missing not in EpicUpdateChecker._parse_update_output(_REAL_SCAN)


def test_no_updates_parses_empty() -> None:
    """A clean library reports nothing, not a spurious entry."""
    clean = (
        "\nInstalled games:\n"
        " * River City Girls 2 (App name: 972f47c7 | Version: v.1 | "
        "Platform: Windows | 6.45 GiB)\n\nTotal: 1\n"
    )
    assert EpicUpdateChecker._parse_update_output(clean) == []


@pytest.mark.asyncio
async def test_update_game_uses_the_real_install_dir() -> None:
    """``base_path`` comes from the top-level ``install_path``.

    Rocket League lives on the SD card. Reading the (non-existent) nested
    ``install`` key made this ``None``, which sent the installer to its
    default internal root — harmless only because legendary reuses the
    existing directory for an installed game and ignores ``--base-path``.
    """
    checker = _checker()
    # MagicMock, not AsyncMock: ``invalidate_installed_cache`` is sync, and
    # an AsyncMock would hand back a coroutine nobody awaits.
    checker._library = MagicMock()  # type: ignore[assignment]
    checker._library.read_installed_map = AsyncMock(
        return_value={
            "Sugar": {
                "app_name": "Sugar",
                "title": "Rocket League®",
                "version": "++Prime+Update59-CL-520762",
                "install_path": "/run/media/deck/microSTEAMDECK/Games/rocketleague",
            },
        },
    )

    installer: Any = AsyncMock()
    installer.install_game = AsyncMock(
        return_value=InstallResult(success=True, store="epic", game_id="Sugar"),
    )

    result = await checker.update_game("Sugar", installer=installer)

    assert result.success
    assert installer.install_game.await_args.kwargs["base_path"] == (
        "/run/media/deck/microSTEAMDECK/Games"
    )


@pytest.mark.asyncio
async def test_update_game_rejects_a_game_that_is_not_installed() -> None:
    """No installed record means there is nothing to update."""
    checker = _checker()
    # MagicMock, not AsyncMock: ``invalidate_installed_cache`` is sync, and
    # an AsyncMock would hand back a coroutine nobody awaits.
    checker._library = MagicMock()  # type: ignore[assignment]
    checker._library.read_installed_map = AsyncMock(return_value={})

    installer: Any = AsyncMock()
    result = await checker.update_game("Sugar", installer=installer)

    assert not result.success
    assert result.error == "not_installed"
    installer.install_game.assert_not_awaited()
