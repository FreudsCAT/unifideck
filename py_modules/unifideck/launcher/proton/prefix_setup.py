"""launcher/proton/prefix_setup.py — the ONE canonical prefix setup.

Historically the "create the Wine prefix + install redistributables" process
had two divergent implementations that could disagree on which Proton to use:

* install-time **warmup** (``services/download/prefix_warmup.py``) ran
  createprefix + compat and, on a runtime hang, retried once with the managed
  GE-Proton — but recorded nothing about the Proton it succeeded with; and
* first **launch** (``services/launcher/orchestrator.py``) ran createprefix
  (Phase 1.5) then compat (inside ``proton.dispatch``) exactly once, with NO
  hang recovery and NO record.

So warmup could recover to GE-Proton while launch independently re-picked the
user's (hanging) global-default Proton, saw a "Proton family change", wiped the
just-warmed prefix, and re-ran the whole setup at Play time — throwing warmup's
work away (observed live: Rise of the Tomb Raider, 2026-07-22).

:func:`setup_prefix` is the single source of truth both paths now call. It runs
the identical, self-healing setup and — crucially — **pins the Proton it
actually succeeded with** (``proton_settings.json`` tier-1 + the prefix's
``.unifideck_proton_version`` marker) so the next launch resolves the SAME
Proton and does a fast no-op instead of a full redo. Whichever path runs first
wins and pins; the other becomes a genuine no-op.

Lives in ``launcher/`` (not ``services/``) because it runs under the system
``/usr/bin/python3`` out-of-process: stdlib-only at import time. The one write
into the aiohttp-heavy ``compatibility`` package (``save_proton_setting``) is
imported lazily inside the function, exactly as ``compat/ge_fallback.py`` does.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.launcher.types.context import LaunchContext, RuntimeState

logger = logging.getLogger(__name__)


async def _run_one(
    ctx: LaunchContext,
    state: RuntimeState,
    python_bin: Any,
    proton: tuple[Path, str],
    session_env: dict[str, str] | None,
) -> bool:
    """createprefix + generic compat under one Proton; True if a step hung.

    ``proton`` is a ``(path, tool_id)`` pair. Best-effort: any failure is
    logged and swallowed so the caller can still fall through to the GE retry
    (and the launch-time path remains a last-resort fallback).
    """
    from unifideck.launcher.proton import proton_prepare
    from unifideck.launcher.proton.compat import apply_prefix_compat
    from unifideck.launcher.proton.compat.prefix_init import (
        ensure_prefix_initialized,
    )

    proton_path, proton_tool_id = proton
    plan = proton_prepare(
        ctx, state, python_bin=python_bin,
        proton_path=proton_path, proton_tool_id=proton_tool_id,
    )
    # Graft any caller-supplied session env (install-time warmup borrows the
    # user session from the running Steam client; at launch Steam already
    # provides it so this is None). ``setdefault`` never clobbers a value the
    # plan already carries.
    if session_env:
        for env_key, env_val in session_env.items():
            plan.env.setdefault(env_key, env_val)
    try:
        await ensure_prefix_initialized(plan)
        _bridge_into_compatdata(plan)
        return await apply_prefix_compat(plan)
    except Exception:
        logger.exception(
            "[prefix_setup] prefix init/compat failed for %s (continuing)",
            ctx.game_key,
        )
        return False


def _bridge_into_compatdata(plan: Any) -> None:
    """Expose this prefix to external Wine tooling (Protontricks).

    Protontricks resolves a non-Steam shortcut's prefix only at
    ``steamapps/compatdata/<appid>``, which is nowhere near where we keep
    ours, so without this link it reports "does not have a prefix" and skips
    the game entirely. Doing it here rather than at install time also repairs
    prefixes that predate the bridge, and covers Ubisoft, whose prefix path is
    only known once resolved at launch.

    ``ctx.steam_app_id`` comes straight from the games.map v3 row — never
    recompute it: ``generate_app_id`` is anchored on the launcher exe path, so
    a derived id does not match the stored one.
    """
    app_id = getattr(getattr(plan, "context", None), "steam_app_id", None)
    if not app_id:
        return
    try:
        from unifideck.core.compat_bridge import link_prefix
        from unifideck.utils.vdf_compat import resolve_live_steam_root

        link_prefix(plan.prefix_path, app_id, resolve_live_steam_root())
    except Exception:
        logger.exception("[prefix_setup] compatdata bridge failed (non-fatal)")


def _pin_final_tool(ctx: LaunchContext, tool: str) -> None:
    """Persist ``tool`` as this game's Proton so the next launch reuses it.

    Called only after a GE recovery, when the tool that succeeded differs from
    what ``select_proton_version`` would resolve again (the user's hanging
    global-default). Without this the next launch re-picks the hanging Proton,
    sees a "Proton family change" against the GE-built prefix, and wipes +
    rebuilds it — exactly the redo-at-Play this module exists to kill.

    Mirrors ``compat/ge_fallback.py``: re-stamp the prefix marker AND write the
    per-game pin. ``save_proton_setting`` lives in the aiohttp-heavy
    ``compatibility`` package, so import it lazily to keep this launcher module
    stdlib-safe at import time. Best-effort — a failed pin must never break
    setup (the prefix is already built; worst case is a redo next launch).
    """
    from unifideck.launcher.proton.compat.prefix_init import _MARKER_NAME

    prefix_root = Path(
        "~/.local/share/unifideck/prefixes",
    ).expanduser() / ctx.game_id
    with contextlib.suppress(OSError):
        prefix_root.mkdir(parents=True, exist_ok=True)
        (prefix_root / _MARKER_NAME).write_text(tool, encoding="utf-8")
    try:
        from unifideck.compatibility.proton_helpers import save_proton_setting

        save_proton_setting(ctx.game_key, tool)
        logger.info(
            "[prefix_setup] pinned %s for %s (survives next launch)",
            tool, ctx.game_key,
        )
    except Exception:
        logger.exception(
            "[prefix_setup] failed to pin %s for %s (non-fatal)",
            tool, ctx.game_key,
        )


def _can_run_winetricks_verb(proton_path: Path | str | None) -> bool:
    """Whether ``umu-run winetricks`` can work with this Proton.

    umu execs ``<PROTONPATH>/protonfixes/winetricks``. GE-Proton and
    UMU-Proton bundle that; official Valve Protons do not ship a
    ``protonfixes`` directory at all, so the verb dies with a
    FileNotFoundError from inside umu rather than reporting anything useful.

    Returns True when there is no path to judge (``None``) or the check
    itself errors, so this gate can only ever skip an attempt that was
    certain to fail — it never becomes a new way to reject a Proton that
    might have worked. A path that exists but has no ``protonfixes/``
    returns False, which includes the "selector handed us something that
    isn't there" case: routing that to managed GE is the same outcome the
    timeout ladder would have reached, just sooner.
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


async def _preempt_incapable_proton(
    ctx: LaunchContext,
    state: RuntimeState,
    python_bin: Path | str,
    default: tuple[Path | str | None, str],
    session_env: dict[str, str] | None,
) -> str | None:
    """Run setup under managed GE when the default can't do compat at all.

    Returns the GE tool id if it took over, else ``None`` (caller proceeds
    with the default as usual).

    umu's winetricks verb execs ``<PROTONPATH>/protonfixes/winetricks``,
    which only GE-Proton and UMU-Proton ship — umu's own ``--help`` says
    "requires UMU-Proton or GE-Proton". Under an official Valve Proton
    (Experimental, Proton 9, proton-cachyos…) that path does not exist, so
    the step cannot succeed no matter how long it is given.

    Attempting it anyway is not merely futile, it is expensive: the missing
    directory surfaces as a FileNotFoundError *inside* umu, the wine child is
    left holding the prefix, and each step burns its full timeout before
    being killed — twice per install — after which the caller's ladder
    switches to GE and RESETS the prefix, discarding everything it just
    built. The end state was always GE, so checking first costs one ``stat``
    and saves minutes plus a wasted prefix build on every fresh install.
    """
    default_path, default_tool = default
    if _can_run_winetricks_verb(default_path):
        return None

    from unifideck.launcher.proton import select_managed_ge_proton

    ge_path, ge_tool = select_managed_ge_proton()
    if ge_tool == default_tool:
        # Nothing better to switch to; let the normal path report whatever
        # actually happens rather than silently doing nothing.
        return None

    logger.info(
        "[prefix_setup] proton=%s cannot run umu's winetricks verb (no "
        "protonfixes/ — official Valve Protons don't ship it); using managed "
        "GE-Proton %s for %s instead of timing out",
        default_tool, ge_tool, ctx.game_key,
    )
    await _run_one(ctx, state, python_bin, (ge_path, ge_tool), session_env)
    _pin_final_tool(ctx, ge_tool)
    return ge_tool


async def setup_prefix(
    ctx: LaunchContext,
    state: RuntimeState,
    *,
    session_env: dict[str, str] | None = None,
) -> tuple[str, bool]:
    """The canonical prefix setup, reused by install warmup AND first launch.

    createprefix + generic compat under the normally-resolved Proton; on a
    runtime **hang** (a step force-killed for exceeding its timeout — a
    structurally-complete but broken Proton the static check can't catch),
    retry ONCE with the plugin-managed GE-Proton, then **pin** whichever Proton
    succeeded so the next launch reuses it directly (no prefix reset, no
    dependency reinstall).

    Recovery ladder, gated so it never loops:
      1. Setup under ``select_proton_version`` (the default a launch would pick).
      2. On hang → switch to ``select_managed_ge_proton`` and retry; pin the
         result.

    Returns ``(final_tool_id, did_recover)``. All best-effort: if every attempt
    still hangs, the prefix finishes at first launch (the launch path re-runs
    these same steps). ``session_env`` is grafted into the umu env for the
    headless install-time caller; ``None`` at launch (Steam provides a session).
    """
    from unifideck.launcher.proton import (
        find_python_3_10_plus,
        select_managed_ge_proton,
        select_proton_version,
    )

    python_bin = find_python_3_10_plus()
    # No per-game Force-Compat choice / steam_app_id is meaningful at install
    # time, and at launch ``ctx.steam_app_id`` is honoured — this resolves the
    # same default the first launch would pick.
    default_path, default_tool = select_proton_version(
        steam_app_id=ctx.steam_app_id, store_game_id=ctx.game_key,
    )

    preempted = await _preempt_incapable_proton(
        ctx, state, python_bin, (default_path, default_tool), session_env,
    )
    if preempted is not None:
        return preempted, True

    if not await _run_one(
        ctx, state, python_bin, (default_path, default_tool), session_env,
    ):
        return default_tool, False

    ge_path, ge_tool = select_managed_ge_proton()
    if ge_tool == default_tool:
        logger.warning(
            "[prefix_setup] compat timed out for %s under managed GE-Proton "
            "%s — not retrying (prefix finishes at launch)",
            ctx.game_key, ge_tool,
        )
        return ge_tool, False

    logger.warning(
        "[prefix_setup] compat still timing out for %s under proton=%s — "
        "retrying setup with managed GE-Proton %s",
        ctx.game_key, default_tool, ge_tool,
    )
    await _run_one(ctx, state, python_bin, (ge_path, ge_tool), session_env)
    _pin_final_tool(ctx, ge_tool)
    return ge_tool, True
