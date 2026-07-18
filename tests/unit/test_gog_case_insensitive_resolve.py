"""GOG manifest paths must resolve against a case-sensitive filesystem.

GOG's ``goggame-*.info`` manifests are authored on Windows (case-
insensitive/case-preserving). Confirmed against real GOG DOSBox packages
("Betrayal at Krondor", "Caesar II") that the manifest's playTask ``path``
can be lowercase (``DOSBOX\\dosbox.exe``) while the actual extracted file
is mixed-case (``DOSBOX/DOSBox.exe``). A naive case-sensitive path join
never finds the file, so the whole manifest-driven resolution in
``GOGExeResolver._resolve_via_goggame_info`` silently falls through to
the much less reliable "largest .exe" heuristic — losing the playTask's
``arguments`` (see ``compat/gog.py::_read_required_launch_args``, which
reuses ``resolve_case_insensitive`` for the same reason) along the way.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from unifideck.stores.gog.exe_resolver import (
    GOGExeResolver,
    resolve_case_insensitive,
)


def test_corrects_case_for_a_single_segment(tmp_path):
    (tmp_path / "DOSBOX").mkdir()
    (tmp_path / "DOSBOX" / "DOSBox.exe").write_bytes(b"")

    resolved = resolve_case_insensitive(tmp_path, "DOSBOX\\dosbox.exe")

    assert resolved == str(tmp_path / "DOSBOX" / "DOSBox.exe")


def test_corrects_case_for_every_segment(tmp_path):
    (tmp_path / "MyGame" / "Bin").mkdir(parents=True)
    (tmp_path / "MyGame" / "Bin" / "Launcher.EXE").write_bytes(b"")

    resolved = resolve_case_insensitive(tmp_path, "mygame\\bin\\launcher.exe")

    assert resolved == str(tmp_path / "MyGame" / "Bin" / "Launcher.EXE")


def test_already_correct_case_is_unchanged(tmp_path):
    (tmp_path / "DOSBOX").mkdir()
    (tmp_path / "DOSBOX" / "dosbox.exe").write_bytes(b"")

    resolved = resolve_case_insensitive(tmp_path, "DOSBOX\\dosbox.exe")

    assert resolved == str(tmp_path / "DOSBOX" / "dosbox.exe")


def test_no_match_falls_back_to_naive_join(tmp_path):
    (tmp_path / "DOSBOX").mkdir()
    # No file at all under DOSBOX/ — nothing to case-correct against.
    resolved = resolve_case_insensitive(tmp_path, "DOSBOX\\missing.exe")

    assert resolved == str(tmp_path / "DOSBOX" / "missing.exe")


def test_missing_intermediate_directory_falls_back_to_naive_join(tmp_path):
    resolved = resolve_case_insensitive(tmp_path, "NoSuchDir\\dosbox.exe")

    assert resolved == str(tmp_path / "NoSuchDir" / "dosbox.exe")


def test_gog_exe_resolver_finds_mismatched_case_via_goggame_info(tmp_path, caplog):
    """End-to-end: the real "Betrayal at Krondor" / "Caesar II" scenario.

    Before the fix this silently fell through to the largest-exe
    heuristic (logged as "resolved via goggame info" NEVER appearing);
    after the fix, resolution succeeds via the manifest path.
    """
    info = {
        "gameId": "123",
        "playTasks": [
            {
                "isPrimary": True,
                "type": "FileTask",
                "path": "DOSBOX\\dosbox.exe",
                "workingDir": "DOSBOX",
                "arguments": '-conf "..\\game.conf" -noconsole -c "exit"',
            },
        ],
    }
    (tmp_path / "goggame-123.info").write_text(json.dumps(info))
    (tmp_path / "DOSBOX").mkdir()
    (tmp_path / "DOSBOX" / "DOSBox.exe").write_bytes(b"")
    (tmp_path / "game.conf").write_text("")

    with caplog.at_level(
        logging.INFO, logger="unifideck.stores.gog.exe_resolver",
    ):
        result = GOGExeResolver().find_with_workdir(str(tmp_path))

    assert result == (
        str(tmp_path / "DOSBOX" / "DOSBox.exe"),
        str(tmp_path / "DOSBOX"),
    )
    assert any(
        "resolved via goggame info" in rec.message for rec in caplog.records
    )
    assert not any("ambiguous" in rec.message for rec in caplog.records)
