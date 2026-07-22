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
        return await apply_prefix_compat(plan)
    except Exception:
        logger.exception(
            "[prefix_setup] prefix init/compat failed for %s (continuing)",
            ctx.game_key,
        )
        return False


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
