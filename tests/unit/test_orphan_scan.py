"""Unit tests for the boot-time orphaned-shortcut classifier.

Exercises ``orphan_scan.classify_orphan`` / ``scan_orphans`` directly —
the pure decision table that finds Unifideck shortcuts the post-sync
reconcile can't see (it keys off ``LaunchOptions``, never the ``Exe``
field). The load-bearing safety property is that protected/auth,
healthy, and foreign shortcuts are never classified for deletion.
"""
from __future__ import annotations

from unifideck.services.shortcut.orphan_scan import (
    classify_orphan,
    scan_orphans,
)

# Production launcher path (matches what service_defs wires up).
LAUNCHER = "/home/deck/homebrew/plugins/unifideck/bin/unifideck-launcher"
# A different install root — proves basename matching, not path equality
# (the reporter's screenshot showed /home/reboot/...).
LAUNCHER_OTHER = "/home/reboot/homebrew/plugins/Unifideck/bin/unifideck-launcher"


def _entry(exe: str, launch: str, appid: int = 123, **extra) -> dict:
    """Build a minimal shortcuts.vdf entry dict."""
    e = {"appid": appid, "Exe": exe, "LaunchOptions": launch}
    e.update(extra)
    return e


# --- Type A : our launcher, no resolvable id -> delete -----------------


def test_type_a_launcher_exe_empty_launch_options_is_deleted():
    entry = _entry(f'"{LAUNCHER}"', "", appid=42)
    result = classify_orphan(entry, LAUNCHER)
    assert result is not None
    kind, payload = result
    assert kind == "delete"
    assert payload["appid_unsigned"] == 42


def test_type_a_garbage_launch_options_is_deleted():
    entry = _entry(f'"{LAUNCHER}"', "no store token here")
    kind, _ = classify_orphan(entry, LAUNCHER)  # type: ignore[misc]
    assert kind == "delete"


def test_type_a_py_launcher_basename_is_deleted():
    entry = _entry(f'"{LAUNCHER}.py"', "")
    kind, _ = classify_orphan(entry, LAUNCHER)  # type: ignore[misc]
    assert kind == "delete"


def test_type_a_matches_across_install_roots_by_basename():
    """A shortcut created on another machine's plugin dir still matches."""
    entry = _entry(f'"{LAUNCHER_OTHER}"', "")
    # launcher_path here is the *local* path; basename still matches.
    kind, _ = classify_orphan(entry, LAUNCHER)  # type: ignore[misc]
    assert kind == "delete"


# --- Type B : valid id, missing/foreign exe -> recover -----------------


def test_type_b_empty_exe_with_valid_launch_options_is_recover():
    entry = _entry("", "epic:some-game")
    result = classify_orphan(entry, LAUNCHER)
    assert result is not None
    kind, payload = result
    assert kind == "recover"
    assert payload["store"] == "epic"
    assert payload["game_id"] == "some-game"
    assert payload["full_id"] == "epic:some-game"


def test_type_b_foreign_exe_with_valid_launch_options_is_recover():
    entry = _entry('"/home/deck/Heroic/some.exe"', "gog:12345")
    kind, payload = classify_orphan(entry, LAUNCHER)  # type: ignore[misc]
    assert kind == "recover"
    assert payload["full_id"] == "gog:12345"


# --- Leave-alone cases -------------------------------------------------


def test_healthy_shortcut_is_left_alone():
    entry = _entry(f'"{LAUNCHER}"', "epic:some-game")
    assert classify_orphan(entry, LAUNCHER) is None


def test_healthy_shortcut_with_user_params_is_left_alone():
    entry = _entry(f'"{LAUNCHER}"', "epic:some-game MANGOHUD=1")
    assert classify_orphan(entry, LAUNCHER) is None


def test_foreign_shortcut_is_left_alone():
    # A user's own non-Steam shortcut: foreign exe, empty launch options.
    entry = _entry('"/home/deck/Games/MyGame/game.exe"', "")
    assert classify_orphan(entry, LAUNCHER) is None


# --- Protected / auth shortcuts (never touched) ------------------------


def test_protected_auth_id_never_classified_even_when_orphaned():
    # epic:epic-auth with empty exe would look like Type B, but it's
    # protected — the auth flow owns it.
    entry = _entry("", "epic:epic-auth")
    assert classify_orphan(entry, LAUNCHER) is None


def test_protected_auth_id_with_launcher_exe_left_alone():
    entry = _entry(f'"{LAUNCHER}"', "ubisoft:upc-auth")
    assert classify_orphan(entry, LAUNCHER) is None


def test_protected_auth_prefix_id_left_alone():
    entry = _entry("", "epic:auth-2026-05-18T00:00:00")
    assert classify_orphan(entry, LAUNCHER) is None


def test_auth_tag_left_alone_even_without_protected_id():
    # No id at all, but an auth-* tag marks it as auth-owned.
    entry = _entry(f'"{LAUNCHER}"', "", tags={"0": "auth-epic"})
    assert classify_orphan(entry, LAUNCHER) is None


# --- appid handling ----------------------------------------------------


def test_negative_signed_appid_converted_to_unsigned():
    entry = _entry(f'"{LAUNCHER}"', "", appid=-1)
    _, payload = classify_orphan(entry, LAUNCHER)  # type: ignore[misc]
    assert payload["appid_unsigned"] == (-1 + 2 ** 32)


def test_missing_appid_skipped():
    entry = {"Exe": f'"{LAUNCHER}"', "LaunchOptions": ""}
    assert classify_orphan(entry, LAUNCHER) is None


def test_non_int_appid_skipped():
    entry = _entry(f'"{LAUNCHER}"', "", appid="not-an-int")  # type: ignore[arg-type]
    assert classify_orphan(entry, LAUNCHER) is None


# --- Robustness --------------------------------------------------------


def test_exe_quote_and_whitespace_robustness():
    entry = _entry(f'  "{LAUNCHER}"  ', "")
    kind, _ = classify_orphan(entry, LAUNCHER)  # type: ignore[misc]
    assert kind == "delete"


def test_lowercase_exe_key_also_recognised():
    entry = {"appid": 7, "exe": f'"{LAUNCHER}"', "LaunchOptions": ""}
    kind, _ = classify_orphan(entry, LAUNCHER)  # type: ignore[misc]
    assert kind == "delete"


def test_non_dict_entry_returns_none():
    assert classify_orphan("not a dict", LAUNCHER) is None  # type: ignore[arg-type]
    assert classify_orphan(None, LAUNCHER) is None  # type: ignore[arg-type]


# --- Aggregation -------------------------------------------------------


def test_scan_orphans_partitions_a_mixed_root():
    root = {
        "0": _entry(f'"{LAUNCHER}"', "", appid=1),                # Type A
        "1": _entry("", "epic:g1", appid=2),                     # Type B
        "2": _entry(f'"{LAUNCHER}"', "gog:g2", appid=3),         # healthy
        "3": _entry('"/x/heroic.exe"', "", appid=4),             # foreign
        "4": _entry("", "epic:epic-auth", appid=5),              # protected
        "5": _entry(f'"{LAUNCHER}"', "garbage", appid=6),        # Type A
    }
    result = scan_orphans(root, LAUNCHER)
    delete_ids = {e["appid_unsigned"] for e in result["delete"]}
    recover_ids = {e["appid_unsigned"] for e in result["recover"]}
    assert delete_ids == {1, 6}
    assert recover_ids == {2}


def test_scan_orphans_tolerates_non_dict_root():
    assert scan_orphans(None, LAUNCHER) == {"delete": [], "recover": []}
    assert scan_orphans([], LAUNCHER) == {"delete": [], "recover": []}


def test_scan_orphans_skips_non_dict_entries():
    root = {"0": "junk", "1": _entry(f'"{LAUNCHER}"', "", appid=9)}
    result = scan_orphans(root, LAUNCHER)
    assert {e["appid_unsigned"] for e in result["delete"]} == {9}
