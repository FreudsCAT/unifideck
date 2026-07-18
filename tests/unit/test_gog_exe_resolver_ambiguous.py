"""GOGExeResolver's largest-exe fallback logs when its guess is ambiguous.

``_resolve_via_largest_exe`` picks "the biggest .exe" when nothing more
reliable (goggame-*.info, start.sh) is found — this is exactly the class
of heuristic that can pick a DOSBox install's ``dosbox.exe`` (a real,
often-large binary) over the actual game. This doesn't change the pick
(Unifideck has no UI to punt an ambiguous choice to), but a wrong guess
should be traceable in the logs instead of invisible.
"""
from __future__ import annotations

import logging

from unifideck.stores.gog.exe_resolver import GOGExeResolver


def test_warns_when_another_candidate_is_close_in_size(tmp_path, caplog):
    (tmp_path / "dosbox.exe").write_bytes(b"0" * (10 * 1024 * 1024))
    (tmp_path / "mygame.exe").write_bytes(b"0" * (9 * 1024 * 1024))

    with caplog.at_level(logging.WARNING, logger="unifideck.stores.gog.exe_resolver"):
        result = GOGExeResolver().find(str(tmp_path))

    assert result == str(tmp_path / "dosbox.exe")
    assert any("ambiguous" in rec.message for rec in caplog.records)


def test_no_warning_when_one_candidate_clearly_dominates(tmp_path, caplog):
    (tmp_path / "mygame.exe").write_bytes(b"0" * (50 * 1024 * 1024))
    (tmp_path / "tool.exe").write_bytes(b"0" * (1024 * 1024))

    with caplog.at_level(logging.WARNING, logger="unifideck.stores.gog.exe_resolver"):
        result = GOGExeResolver().find(str(tmp_path))

    assert result == str(tmp_path / "mygame.exe")
    assert not any("ambiguous" in rec.message for rec in caplog.records)
