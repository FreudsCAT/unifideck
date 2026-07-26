"""Regression: both loader vars are scrubbed at the umu spawn point.

Bug report (SteamOS 3.8.16): every GOG game failed to launch with umu
exit code 127. game.log showed umu starting cleanly, then, right after
``pressure-vessel-wrap``::

    python3: error while loading shared libraries: libz.so.1:
    cannot open shared object file: No such file or directory

That ``python3`` is the interpreter of Proton's own launch script, run
*inside* the container. It died because an inherited ``LD_LIBRARY_PATH``
was copied by umu into ``STEAM_RUNTIME_LIBRARY_PATH``, so a *host* library
path shadowed the container's own libs. (The reporter was on plain Steam
stable, so a containerised Steam client is NOT the source; where the value
comes from is unestablished. It must not survive either way.)

Epic was the only store that worked, purely because
``handlers/epic.py`` wraps its umu-run invocation in
``env -u LD_LIBRARY_PATH -u LD_PRELOAD``. Everything else — GOG, Amazon,
Ubisoft, raw-exe, plus the winetricks/vcruntime compat steps — reaches
``_run_umu_once`` directly, so the scrub belongs there: one choke point,
every store covered.

See also test_proton_env_sanitize.py, which stops
``sanitize_frozen_loader_env`` from resurrecting ``LD_LIBRARY_PATH`` out
of ``LD_LIBRARY_PATH_ORIG`` in the first place.
"""
from __future__ import annotations

from unifideck.launcher.proton.infrastructure import umu_runtime as ur


async def _child_env(env: dict[str, str], tmp_path):
    """Run a child with ``env`` through the umu helper; return its real env."""
    dump = tmp_path / "env.txt"
    argv = ["/bin/sh", "-c", f"env > {dump}"]

    rc = await ur.run_umu_with_retry(argv, env=env)

    assert rc == 0
    return dict(
        line.split("=", 1)  # type: ignore[misc]
        for line in dump.read_text().splitlines()
        if "=" in line
    )


async def test_ld_library_path_never_reaches_umu(tmp_path):
    """The exact failing shape: a containerised-Steam LD_LIBRARY_PATH."""
    child = await _child_env(
        {
            "PATH": "/usr/bin:/bin",
            "LD_LIBRARY_PATH": (
                "/usr/lib/pressure-vessel/overrides/lib/x86_64-linux-gnu/aliases"
            ),
        },
        tmp_path,
    )

    assert "LD_LIBRARY_PATH" not in child
    assert child["PATH"] == "/usr/bin:/bin"


async def test_ld_preload_never_reaches_umu(tmp_path):
    """The pre-existing LD_PRELOAD guard still holds."""
    child = await _child_env(
        {
            "PATH": "/usr/bin:/bin",
            "LD_PRELOAD": "/home/deck/.local/share/Steam/ubuntu12_32/gameoverlayrenderer.so",
        },
        tmp_path,
    )

    assert "LD_PRELOAD" not in child


async def test_scrub_leaves_the_rest_of_the_plan_env_intact(tmp_path):
    """Only the two loader vars are dropped — umu's own config must survive."""
    child = await _child_env(
        {
            "PATH": "/usr/bin:/bin",
            "LD_LIBRARY_PATH": "/host/lib",
            "LD_PRELOAD": "/host/overlay.so",
            "GAMEID": "umu-0",
            "STORE": "gog",
            "PROTONPATH": "/compat/GE-Proton11-3",
            "WINEPREFIX": "/prefixes/1103602225",
        },
        tmp_path,
    )

    assert "LD_LIBRARY_PATH" not in child
    assert "LD_PRELOAD" not in child
    assert child["GAMEID"] == "umu-0"
    assert child["STORE"] == "gog"
    assert child["PROTONPATH"] == "/compat/GE-Proton11-3"
    assert child["WINEPREFIX"] == "/prefixes/1103602225"


async def test_scrub_mutates_caller_dict_so_retry_is_also_clean(tmp_path):
    """attempt 2 must not re-introduce what attempt 1 stripped."""
    env = {"PATH": "/usr/bin:/bin", "LD_LIBRARY_PATH": "/host/lib"}

    await _child_env(env, tmp_path)

    assert "LD_LIBRARY_PATH" not in env


async def test_env_none_is_passed_through_untouched(tmp_path):
    """``env=None`` (inherit) must not crash the scrub."""
    rc = await ur.run_umu_with_retry(["/bin/true"])
    assert rc == 0
