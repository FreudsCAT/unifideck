"""Tests for GOG Linux-depot DOSBox conf detection.

``parse_dosbox_conf_args`` used to hard-depend on regexing GOG's
``start.sh`` for its ``run_dosbox "-conf A" "-conf B"`` call, raising
``ValueError`` (aborting the whole launch) if GOG ever rephrased that
script. It now tries the regex first, falls back to a directory glob
for ``*.conf`` files sitting beside ``start.sh`` (which are real files
regardless of how the script invokes them), and never raises — an
undetectable title returns an empty list so the caller can fall back
to running ``start.sh`` directly instead of hard-failing.
"""
from __future__ import annotations

from unifideck.launcher.proton.handlers.gog_linux_dosbox import (
    parse_dosbox_conf_args,
)


def test_regex_match_takes_priority_over_glob(tmp_path):
    (tmp_path / "dosbox_a.conf").write_text("")
    (tmp_path / "dosbox_b.conf").write_text("")
    start_sh = tmp_path / "start.sh"
    start_sh.write_text(
        '#!/bin/bash\n'
        'run_dosbox "dosbox_a.conf" "dosbox_b.conf"\n',
    )

    assert parse_dosbox_conf_args(start_sh) == [
        "dosbox_a.conf", "dosbox_b.conf",
    ]


def test_falls_back_to_glob_when_run_dosbox_not_found(tmp_path):
    (tmp_path / "game.conf").write_text("")
    (tmp_path / "override.conf").write_text("")
    start_sh = tmp_path / "start.sh"
    # A rephrased script the regex doesn't recognize at all.
    start_sh.write_text("#!/bin/bash\nexec ./dosbox/dosbox_x86_64 \"$@\"\n")

    result = parse_dosbox_conf_args(start_sh)

    assert result == sorted(str(p) for p in tmp_path.glob("*.conf"))
    assert len(result) == 2


def test_returns_empty_list_when_neither_regex_nor_glob_find_anything(
    tmp_path,
):
    start_sh = tmp_path / "start.sh"
    start_sh.write_text("#!/bin/bash\necho no dosbox here\n")

    # Must not raise — callers fall back to running start.sh directly.
    assert parse_dosbox_conf_args(start_sh) == []
