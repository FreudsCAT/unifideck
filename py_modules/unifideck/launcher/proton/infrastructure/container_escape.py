"""Escape Steam's pressure-vessel container before spawning umu.

Unifideck shortcuts point their ``Exe`` at ``bin/unifideck-launcher``, a
native Linux script that selects and drives Proton itself via umu. When the
user sets Steam's own **Properties > Compatibility > "Force the use of a
specific Steam Play compatibility tool"** on such a shortcut — which is a
supported, intended workflow: that is how the user tells us which Proton
they want, and the launcher reads it back via
``selector.get_steam_compat_tool_override`` — Steam ALSO wraps the launcher
itself in its own Steam-Linux-Runtime pressure-vessel container before
spawning it.

Everything the launcher then does happens *inside* steamrt, and Proton's own
``python3`` cannot resolve ``libz.so.1`` there::

    python3: error while loading shared libraries: libz.so.1:
    cannot open shared object file: No such file or directory

umu exits 127. Reproduced deterministically on-device by entering the
container exactly as Steam does
(``SteamLinuxRuntime_4/_v2-entry-point --verb=run --``) and running the
identical umu command: the interpreter reported by umu flips from the host's
``3.13.5 (main, Jun 21 2025) [GCC 15.1.1]`` to the container's
``3.13.5 (main, May 5 2026) [GCC 14.2.0]``, and the launch dies. Escaping and
re-running the *same* command succeeds (rc=0, ProtonFixes + ntsync reached).

``UMU_NO_RUNTIME=1`` does NOT help — verified; the failure is Proton's own
interpreter inside steamrt, not merely umu nesting a second container.

The fix is to hop back out via ``steam-runtime-launch-client
--alongside-steam``, which runs the command in the Steam client's own
context outside the container. Verified on-device:

* the escaped process gets a clean HOST environment (host ``PATH``, no
  ``container``/``PRESSURE_VESSEL_*``/``LD_LIBRARY_PATH`` leakage), plus
  Steam's display/audio session vars — which is exactly why
  ``--alongside-steam`` is used rather than ``--host``;
* exit codes propagate verbatim (checked with a deliberate ``exit 42``);
* killing the client reaps the escaped child, so the launcher's existing
  Stop / cancel / timeout teardown keeps working.

This makes the launch correct **whether or not** Steam wraps us, so the user
can freely switch Proton builds in Steam's own picker to find one that works
for a given game — no clearing of their selection required.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_ESCAPE_CLIENT = "steam-runtime-launch-client"

# Forwarded even when identical to the container's own value, because the
# escaped process starts from Steam's environment rather than ours and these
# are the variables umu/Proton cannot run without.
_ALWAYS_FORWARD = frozenset({
    "GAMEID",
    "PROTONPATH",
    "PROTON_VERB",
    "STEAM_COMPAT_DATA_PATH",
    "STEAM_COMPAT_INSTALL_PATH",
    "STORE",
    "WINEPREFIX",
})


def in_pressure_vessel() -> bool:
    """Whether this process is running inside a pressure-vessel container.

    ``container=pressure-vessel`` is set by pressure-vessel itself;
    ``/run/pressure-vessel`` is its runtime state dir. Both are absent on
    the host (verified on-device), so this is false for the normal
    unwrapped launch and the escape below is a no-op.
    """
    return (
        # SIM112: genuinely lowercase — pressure-vessel/systemd set
        # ``container=pressure-vessel`` (OCI convention), verified on-device.
        # Capitalising it would simply never match.
        os.environ.get("container") == "pressure-vessel"  # noqa: SIM112
        or Path("/run/pressure-vessel").is_dir()
    )


def _forwarded_env(env: dict[str, str] | None) -> list[str]:
    """``KEY=value`` pairs to carry across the container boundary.

    ``env`` is built as ``dict(os.environ)`` plus the launcher's own
    mutations (see ``infrastructure.core._build_umu_env``), so anything that
    still equals ``os.environ`` is an inherited *container* value — dropping
    it lets the clean host value win (notably ``PATH``, which would
    otherwise point at container paths). What remains is precisely what this
    launch deliberately set, plus the always-forward set above.
    """
    base = os.environ
    return [
        f"{key}={value}"
        for key, value in sorted((env or {}).items())
        if key in _ALWAYS_FORWARD or base.get(key) != value
    ]


def escape_argv(
    argv: list[str],
    env: dict[str, str] | None,
    cwd: Path | None = None,
) -> list[str]:
    """Wrap ``argv`` so it runs outside Steam's container, or return it as-is.

    Returns ``argv`` unchanged when not containerised, or when the escape
    client is unavailable — in that case the launch proceeds exactly as
    before (and fails the way it used to) rather than breaking outright.

    ``--directory`` is passed explicitly: the client's own cwd does not
    become the remote process's cwd, and several stores depend on the game
    running from its install dir.
    """
    if not in_pressure_vessel():
        return argv
    client = shutil.which(_ESCAPE_CLIENT)
    if client is None:
        logger.warning(
            "[launcher.umu] inside a pressure-vessel container but %s is "
            "not on PATH — cannot escape; umu will likely fail with rc=127 "
            "(libz.so.1). Clear Steam's Properties > Compatibility override "
            "for this shortcut as a workaround.", _ESCAPE_CLIENT,
        )
        return argv
    escaped = [client, "--alongside-steam"]
    if cwd is not None:
        escaped.append(f"--directory={cwd}")
    escaped.extend(["--", "env", *_forwarded_env(env), *argv])
    logger.info(
        "[launcher.umu] inside Steam's pressure-vessel container "
        "(Force-Compat is set on this shortcut) — escaping via %s so umu "
        "runs on the host", _ESCAPE_CLIENT,
    )
    return escaped
