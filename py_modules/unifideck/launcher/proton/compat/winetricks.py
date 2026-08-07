"""compat/winetricks.py — first-launch Windows redistributables.

Installs the redistributables a Windows game needs (VC++ runtimes,
d3dcompiler, mfc140, …) into its Proton prefix via
``umu-run winetricks <pkgs>``, exactly once per prefix (marker-guarded).
The package list comes from :mod:`game_fixes` — manual overrides →
umu-database protonfixes → global defaults. Generic: runs for every
Windows store, not just Epic.
"""
from __future__ import annotations

import logging
from pathlib import Path

from unifideck.launcher.frontend_bridge import launcher_toast
from unifideck.launcher.proton.fixes.game_fixes import get_required_winetricks
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import (
    UMU_TIMEOUT_RC,
    run_umu_with_retry,
)

logger = logging.getLogger(__name__)

_MARKER_NAME = "unifideck_winetricks_complete.marker"
# Marker bodies that mean "don't run again" (terminal states).
_TERMINAL_MARKERS = ("complete", "no redistributables", "failed")
# Bounds the winetricks step so a hung Proton/Wine (e.g. a broken
# auto-updated Proton-Experimental build spinning wineserver forever)
# can't wedge the serial install queue during prefix warmup. Generous:
# a cold prefix legitimately extracts several redistributables, but
# finite — the queue must survive a hang. On timeout the step is
# force-killed and treated as failed (the game still launches; the
# prefix finishes at launch).
_WINETRICKS_TIMEOUT_SECONDS = 300.0


def _prefix_root(plan: ProtonLaunchPlan) -> Path:
    """Resolve the prefix root (strip a trailing ``pfx`` segment)."""
    p = plan.prefix_path.resolve()
    while p.name == "pfx":
        p = p.parent
    return p


def _already_done(marker: Path) -> bool:
    """True if a prior run reached a terminal state for this prefix."""
    if not marker.is_file():
        return False
    try:
        body = marker.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return any(m in body for m in _TERMINAL_MARKERS)


def _write_marker(marker: Path, body: str) -> None:
    """Best-effort marker write."""
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(body, encoding="utf-8")
    except OSError as e:
        logger.debug("[compat.winetricks] marker write failed: %s", e)


def _skip_incapable_proton(plan: ProtonLaunchPlan, marker: Path) -> bool:
    """Record + announce that this Proton can't install redistributables.

    The marker body is deliberately NOT one of ``_TERMINAL_MARKERS``, so
    ``_already_done`` keeps returning False and the redistributables install
    for real the moment the prefix runs under a capable Proton again. Returns
    ``False`` — nothing hung, so the caller must not trigger the GE retry
    ladder.
    """
    tool = plan.state.proton_tool_id or "this Proton"
    logger.warning(
        "[compat.winetricks] %s ships no protonfixes/ — skipping "
        "redistributables (umu's winetricks verb needs GE/UMU-Proton). The "
        "prefix stays on the Proton you selected; clear Steam's Force "
        "Compatibility if the game misses a redistributable.", tool,
    )
    _write_marker(marker, "skipped: proton has no protonfixes")
    launcher_toast(
        "toasts.launcher.redistributablesSkippedProton",
        i18n_title_key="toasts.launcher.dependenciesTitle",
        i18n_params={"version": tool},
        game_title=plan.context.game_key,
        severity="warning",
    )
    return False


def _proton_can_run_winetricks_verb(proton_path: str | Path | None) -> bool:
    """Whether ``umu-run winetricks`` can work with this Proton.

    umu passes ``cwd=f"{PROTONPATH}/protonfixes"`` to ``Popen`` for the
    winetricks verb (umu 1.4.4 ``umu_run.py:720-722``), so a Proton without
    that **directory** raises FileNotFoundError inside umu — umu's own
    ``--help`` says the verb "requires UMU-Proton or GE-Proton". Official
    Valve Protons (Experimental, Proton 9/10/11, Hotfix) ship no
    ``protonfixes/``, so the step cannot succeed no matter how long it runs.

    This used to live in ``prefix_setup._preempt_incapable_proton``, where it
    switched the WHOLE prefix setup to managed GE-Proton — and that is what
    broke launching under a user-selected Proton. A prefix is single-Proton
    state: GE's ``wineboot`` rewrites ``<compatdata>/version`` in GE's format,
    which official Proton's ``upgrade_pfx`` cannot parse ("Prefix has an
    invalid version?!"), and the game then ran under the user's Proton against
    a prefix GE had built and stamped. The capability of ONE optional step was
    never evidence about which Proton the user wants, so the gate now skips
    that step and nothing else.

    Fails open (``True``) when there is no path to judge or the check itself
    errors: it can only ever skip an attempt that was certain to fail.
    """
    if not proton_path:
        return True
    try:
        root = Path(proton_path)
        if root.is_file():  # the `proton` script itself was passed
            root = root.parent
        return (root / "protonfixes").is_dir()
    except OSError:
        return True


async def apply_winetricks(plan: ProtonLaunchPlan) -> bool:
    """Install required redistributables once per prefix.

    Best-effort: any failure writes a ``failed`` marker and returns —
    the caller continues to launch the game regardless. Returns ``True``
    only when the umu step was force-killed for exceeding its timeout
    (a hung Proton), so the warmup caller can retry with a good Proton.
    """
    prefix_root = _prefix_root(plan)
    marker = prefix_root / _MARKER_NAME
    if _already_done(marker):
        logger.debug(
            "[compat.winetricks] already done for %s", prefix_root,
        )
        return False

    packages = await get_required_winetricks(plan.context.game_id)
    if not packages:
        _write_marker(marker, "no redistributables")
        return False

    if not _proton_can_run_winetricks_verb(plan.env.get("PROTONPATH")):
        return _skip_incapable_proton(plan, marker)

    logger.info(
        "[compat.winetricks] installing for %s: %s",
        plan.context.game_id, ", ".join(packages),
    )
    _write_marker(marker, "installing: " + ", ".join(packages))
    launcher_toast(
        "toasts.launcher.installingRedistributables",
        i18n_title_key="toasts.launcher.dependenciesTitle",
        game_title=plan.context.game_key,
    )

    # winetricks runs under the same Proton/prefix the game uses. umu's
    # GAMEID=umu-0 (generic, no per-game protonfix) + no runtime update
    # so the redistributable install doesn't churn the umu runtime.
    env = dict(plan.env)
    env["WINEPREFIX"] = str(prefix_root)
    env["GAMEID"] = "umu-0"
    env["UMU_RUNTIME_UPDATE"] = "0"
    # ``run``, not the inherited ``waitforexitandrun``: the latter does
    # ``wineserver -w`` first, which deadlocks against a resident wineserver
    # left by a prior setup step (Proton's steam.exe stub keeps it alive).
    # See prefix_init._ensure_created for the full explanation.
    env["PROTON_VERB"] = "run"
    argv = [
        str(plan.python_bin),
        str(plan.umu_wrapper),
        "winetricks",
        "-q",  # unattended — never block on a GUI prompt
        *packages,
    ]
    try:
        rc = await run_umu_with_retry(
            argv, env=env, timeout=_WINETRICKS_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.exception("[compat.winetricks] run failed")
        _write_marker(marker, "failed: exception")
        return False
    return _handle_winetricks_rc(plan, marker, rc)


def _handle_winetricks_rc(
    plan: ProtonLaunchPlan, marker: Path, rc: int,
) -> bool:
    """Marker/toast bookkeeping for a winetricks rc; True iff it timed out."""
    if rc == 0:
        _write_marker(marker, "complete")
        logger.info(
            "[compat.winetricks] complete for %s", plan.context.game_id,
        )
        launcher_toast(
            "toasts.launcher.redistributablesInstalled",
            i18n_title_key="toasts.launcher.dependenciesReady",
            game_title=plan.context.game_key,
        )
        return False
    if rc == UMU_TIMEOUT_RC:
        # A timeout means the Proton/Wine boot hung (transient — usually
        # a broken auto-updated Proton build). Do NOT write a terminal
        # marker: once a good Proton is selected (see Part A), the next
        # launch should retry the redistributables install rather than
        # skip it forever on a stale "failed" marker.
        logger.warning(
            "[compat.winetricks] timed out for %s — leaving marker "
            "unwritten so a good Proton retries next launch",
            plan.context.game_id,
        )
        launcher_toast(
            "toasts.launcher.checkLogs",
            i18n_title_key="toasts.launcher.dependenciesStatus",
            game_title=plan.context.game_key,
            severity="warning",
        )
        return True
    _write_marker(marker, f"failed: exit {rc}")
    logger.warning(
        "[compat.winetricks] rc=%d for %s",
        rc, plan.context.game_id,
    )
    launcher_toast(
        "toasts.launcher.checkLogs",
        i18n_title_key="toasts.launcher.dependenciesStatus",
        game_title=plan.context.game_key,
        severity="warning",
    )
    return False
