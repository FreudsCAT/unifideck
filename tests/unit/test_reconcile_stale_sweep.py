"""Unit tests for the widened stale-shortcut sweep.

The beta-tester bug: shortcuts for a store that returns no games this
sync (logged-out Ubisoft, the legacy ``microsoft:ms-auth`` row) never
got swept, because the sweep only touched stores present in the synced
library. The fix lets the post-sync reconcile pass the full set of
*registered* stores as ``valid_stores`` so those orphans self-heal.

These tests exercise the decision function
``_ReconcilePhasesMixin._is_stale_managed_shortcut`` directly.
"""
from __future__ import annotations

from unifideck.services.shortcut.games_map import UNIFIDECK_TAG
from unifideck.services.shortcut.reconcile_phases import _ReconcilePhasesMixin

_is_stale = _ReconcilePhasesMixin._is_stale_managed_shortcut


def _managed(launch: str, appid: int) -> dict:
    return {
        "appid": appid,
        "LaunchOptions": launch,
        "tags": {"0": UNIFIDECK_TAG, "1": launch.split(":", 1)[0]},
    }


def test_phantom_ubisoft_swept_when_store_registered():
    """A registered-but-empty store's orphan shortcut is stale."""
    entry = _managed("ubisoft:123", appid=999)
    # ubisoft returned no games (not in valid_app_ids) but IS registered
    assert _is_stale(entry, valid_app_ids=set(), valid_stores={"ubisoft", "epic"})


def test_legacy_ms_auth_swept_when_microsoft_registered():
    """The legacy persistent microsoft:ms-auth row is sweepable."""
    entry = _managed("microsoft:ms-auth", appid=555)
    assert _is_stale(
        entry, valid_app_ids=set(), valid_stores={"microsoft"},
    )


def test_orphan_preserved_when_store_not_in_valid_stores():
    """Narrow valid_stores leaves other stores' shortcuts alone.

    This is the pre-fix behaviour and the reason the widening matters:
    with valid_stores={"epic"} the ubisoft orphan is NOT swept.
    """
    entry = _managed("ubisoft:123", appid=999)
    assert not _is_stale(entry, valid_app_ids=set(), valid_stores={"epic"})


def test_protected_auth_shortcut_never_swept():
    """Protected auth ids survive even with their store registered."""
    entry = _managed("epic:epic-auth", appid=777)
    assert not _is_stale(
        entry, valid_app_ids=set(), valid_stores={"epic", "ubisoft"},
    )


def test_live_game_not_swept():
    """A shortcut whose appid is still valid is kept."""
    entry = _managed("ubisoft:123", appid=999)
    assert not _is_stale(
        entry, valid_app_ids={999}, valid_stores={"ubisoft"},
    )


def test_non_managed_shortcut_ignored():
    """A user's own shortcut (no Unifideck markers) is never swept."""
    entry = {"appid": 42, "LaunchOptions": "", "tags": {}}
    assert not _is_stale(entry, valid_app_ids=set(), valid_stores={"ubisoft"})
