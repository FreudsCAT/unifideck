"""GOG *Windows* DOSBox packages need their playTask ``arguments`` injected.

GitHub #248 / UD-010: GOG's Windows DOSBox installer packages (confirmed
against real "Betrayal at Krondor" / "Caesar II" GOG zips) mark the
*generic* wrapper exe as the primary playTask:

    {
      "isPrimary": true, "path": "DOSBOX\\dosbox.exe", "workingDir": "DOSBOX",
      "arguments": "-conf \"..\\game.conf\" -conf \"..\\game_single.conf\" -noconsole -c \"exit\""
    }

Without reading ``arguments``, ``dosbox.exe`` launches via umu/Proton with
NO ``-conf`` flags at all — the exact "generic DOSBOX .exe" bug the issue
describes, on the Windows path (a *different* code path from the Linux
``start.sh`` depot case, which ``gog_linux_dosbox.py`` already handles).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from unifideck.launcher.proton.compat import gog
from unifideck.launcher.proton.compat.gog import _read_required_launch_args

_CAESAR_II_INFO = {
    "gameId": "1386577474",
    "name": "Caesar II",
    "playTasks": [
        {
            "arguments": (
                '-conf "..\\dosbox_caesar2.conf" '
                '-conf "..\\dosbox_caesar2_single.conf" '
                '-noconsole -c "exit"'
            ),
            "category": "game",
            "isPrimary": True,
            "path": "DOSBOX\\dosbox.exe",
            "type": "FileTask",
            "workingDir": "DOSBOX",
        },
        {
            "arguments": "1386577474",
            "category": "tool",
            "path": "DOSBOX\\GOGDOSConfig.exe",
            "type": "FileTask",
            "workingDir": "DOSBOX",
        },
    ],
}


def _write_install(tmp_path: Path, info: dict) -> Path:
    (tmp_path / f"goggame-{info['gameId']}.info").write_text(json.dumps(info))
    dosbox_dir = tmp_path / "DOSBOX"
    dosbox_dir.mkdir()
    # Real GOG DOSBox packages ("Betrayal at Krondor", "Caesar II" —
    # confirmed on-device) ship the actual binary as ``DOSBox.exe``
    # (mixed case) while the manifest's own ``path`` field says
    # lowercase ``dosbox.exe`` — GOG's manifests are authored on
    # Windows' case-insensitive filesystem. Deliberately mismatched
    # here so these tests catch the case-sensitivity regression a
    # same-case fixture would hide.
    (dosbox_dir / "DOSBox.exe").write_bytes(b"")
    (dosbox_dir / "GOGDOSConfig.exe").write_bytes(b"")
    (tmp_path / "dosbox_caesar2.conf").write_text("")
    (tmp_path / "dosbox_caesar2_single.conf").write_text("")
    return tmp_path


def test_reads_primary_playtask_arguments(tmp_path):
    install_root = _write_install(tmp_path, _CAESAR_II_INFO)
    exe_path = install_root / "DOSBOX" / "DOSBox.exe"

    args = _read_required_launch_args(install_root, exe_path)

    assert args == [
        "-conf", "..\\dosbox_caesar2.conf",
        "-conf", "..\\dosbox_caesar2_single.conf",
        "-noconsole", "-c", "exit",
    ]


def test_matches_the_specific_exe_not_just_any_playtask(tmp_path):
    # The GOGDOSConfig.exe playTask has DIFFERENT arguments (just a game
    # id) — a match must be keyed by resolved exe path, not "first task".
    install_root = _write_install(tmp_path, _CAESAR_II_INFO)
    exe_path = install_root / "DOSBOX" / "GOGDOSConfig.exe"

    args = _read_required_launch_args(install_root, exe_path)

    assert args == ["1386577474"]


def test_returns_empty_list_for_exe_with_no_matching_playtask(tmp_path):
    install_root = _write_install(tmp_path, _CAESAR_II_INFO)
    unrelated_exe = install_root / "DOSBOX" / "other.exe"
    unrelated_exe.write_bytes(b"")

    assert _read_required_launch_args(install_root, unrelated_exe) == []


def test_returns_empty_list_when_no_manifest_present(tmp_path):
    dosbox_dir = tmp_path / "DOSBOX"
    dosbox_dir.mkdir()
    exe_path = dosbox_dir / "DOSBox.exe"
    exe_path.write_bytes(b"")

    assert _read_required_launch_args(tmp_path, exe_path) == []


def test_returns_empty_list_on_unparsable_arguments(tmp_path):
    bad_info = {
        "gameId": "999",
        "playTasks": [
            {
                "isPrimary": True,
                "path": "DOSBOX\\dosbox.exe",
                "arguments": '-conf "unterminated',
            },
        ],
    }
    install_root = _write_install(tmp_path, bad_info)
    exe_path = install_root / "DOSBOX" / "DOSBox.exe"

    assert _read_required_launch_args(install_root, exe_path) == []


async def test_run_umu_exe_threads_required_args_before_user_game_args(
    tmp_path, monkeypatch,
):
    """Integration: ``_run_umu_exe`` puts playTask args before user args.

    Mirrors ``_amazon_launch``'s ordering (fuel_args before
    plan.state.game_args) so a user's own Steam launch options can
    still append to, but never silently replace, the config GOG's own
    manifest says this exe needs.
    """
    install_root = _write_install(tmp_path, _CAESAR_II_INFO)
    exe_path = install_root / "DOSBOX" / "DOSBox.exe"

    captured: dict[str, list[str]] = {}

    async def _fake_run_umu_with_retry(argv, **_kwargs):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(gog, "run_umu_with_retry", _fake_run_umu_with_retry)

    plan = SimpleNamespace(
        state=SimpleNamespace(wrappers=[], game_args=["-windowed"]),
        env={},
        python_bin="/usr/bin/python3",
        umu_wrapper="/umu/umu-run",
        on_process_start=None,
    )

    rc = await gog._run_umu_exe(plan, exe_path, install_root)

    assert rc == 0
    argv = captured["argv"]
    conf_idx = argv.index("-conf")
    windowed_idx = argv.index("-windowed")
    assert conf_idx < windowed_idx
    assert argv[-1] == "-windowed"
    assert str(exe_path) in argv
