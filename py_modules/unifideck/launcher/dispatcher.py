from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types.results import Result

from .types.context import LaunchContext
from .types.errors import GameNotFoundError, LauncherError
from .types.exit_codes import ExitCode

if TYPE_CHECKING:
    from unifideck.services.shortcut import ShortcutService

logger = logging.getLogger(__name__)
def _parse_argv(argv: list[str]) -> tuple[str, str]:
    """Parse argv."""
    if len(argv) < 2:
        raise GameNotFoundError(
            "missing store:game_id argument",
            context={"argv": argv},
        )
    game_key = argv[1]
    if ":" not in game_key:
        raise GameNotFoundError(
            f"malformed game key {game_key!r}, "
            "expected 'store:game_id'",
            context={"game_key": game_key},
        )
    raw_options = " ".join(argv[2:])
    return game_key, raw_options


def _promote_env_tokens(raw_options: str) -> None:
    """Promote ``KEY=value`` tokens from launch options to ``os.environ``.

    Steam passes plugin launch options to the wrapper as argv,
    not as env vars : a shortcut configured with launch options
    ``"amazon:amazon-auth UNIFIDECK_AMAZON_ACTION=auth"`` arrives
    as ``sys.argv[1:] = ["amazon:amazon-auth", "UNIFIDECK_AMAZON_ACTION=auth"]``.

    The auth-detection path (and other downstream code) reads
    these flags from ``os.environ``, so we promote any
    bare ``KEY=value`` token in the joined raw options string
    into the process environment before that code runs.
    Only tokens starting with ``UNIFIDECK_`` are promoted —
    don't pollute the env with arbitrary user-supplied args.
    """
    for token in raw_options.split():
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        if not key.startswith("UNIFIDECK_"):
            continue
        # Don't clobber an existing real env var — caller wins
        # in case Steam ever evolves to pass env vars properly.
        os.environ.setdefault(key, value)
def _resolve_plugin_dir() -> Path:
    """Resolve plugin dir."""
    from unifideck.core.paths import resolve_plugin_dir
    return resolve_plugin_dir(start=Path(__file__))
def _install_path_from_cache(store: str, game_id: str) -> str:
    """Look up an installed game's directory from the library cache.

    Fallback for games whose games.map row is missing entirely (e.g.
    installed by an older build that never wrote one). Returns ``""``
    when the cache is absent or the game isn't found.
    """
    import json
    cache = Path(
        "~/.local/share/unifideck/library_cache.json",
    ).expanduser()
    if not cache.is_file():
        return ""
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    libraries = data.get("libraries") if isinstance(data, dict) else None
    games = (libraries or {}).get(store) or []
    for game in games:
        if isinstance(game, dict) and game.get("store_game_id") == game_id:
            return str(game.get("install_path") or "")
    return ""


def _resolve_exe_from_install(store: str, install_path: str) -> str | None:
    """Re-resolve a launchable target from an install dir.

    Repairs stale/missing games.map rows at launch time. GOG (and
    other) Linux-native games launch via a root ``start.sh`` wrapper;
    everything else resolves the best ``.exe`` via the shared
    ``exe_finder`` heuristic. Best-effort — returns None on failure.

    Deliberately avoids importing any store package (e.g.
    ``GOGExeResolver``): the store ``__init__`` chains pull in auth →
    security → ``cryptography`` whose native ``_cffi_backend`` isn't
    importable in the slim launcher Python, which would crash the
    whole resolution. ``core.exe_finder`` is dependency-light and safe.
    """
    if not install_path:
        return None
    try:
        from unifideck.core.exe_finder import exe_finder

        # Native Linux games (notably GOG) launch via a ``start.sh``
        # wrapper at the install root — ``exe_finder`` only finds
        # ``.exe`` files, so check for it explicitly first.
        start_sh = Path(install_path) / "start.sh"
        if start_sh.is_file():
            return str(start_sh)
        return exe_finder.find(install_path)
    except Exception:
        logger.exception(
            "[launcher.dispatcher] exe re-resolution failed for %s",
            install_path,
        )
        return None


async def _build_context(
    argv: list[str],
    shortcut_svc: ShortcutService,
) -> LaunchContext:
    """Build context."""
    game_key, raw_options = _parse_argv(argv)
    store, game_id = game_key.split(":", 1)

    # Steam passes launch options as argv, not as env vars —
    # promote any ``UNIFIDECK_*=value`` tokens so the rest of
    # the dispatcher (auth detection, downstream services) can
    # read them from ``os.environ`` as it expects.
    _promote_env_tokens(raw_options)

    # Auth-shortcut path : the auth shortcut key is
    # ``<store>:<store>-auth`` and we expect
    # ``UNIFIDECK_<STORE>_ACTION=auth`` in the launch options.
    # There's no games.map entry for it (it's not a game) so we
    # short-circuit the registry lookup and build a context the
    # auth flow can consume directly.
    auth_store, is_launch_action = _detect_auth_action()
    if not is_launch_action:
        logger.info(
            "[launcher.dispatcher] auth shortcut detected: "
            "auth_store=%s game_key=%s",
            auth_store, game_key,
        )
        return LaunchContext(
            store=store,
            game_id=game_id,
            exe_path=Path("/dev/null"),
            work_dir=_resolve_plugin_dir(),
            plugin_dir=_resolve_plugin_dir(),
            raw_options=raw_options,
            is_launch_action=False,
            auth_store=auth_store,
            bypass_circuit_breaker=False,
        )

    entry = await shortcut_svc.get_entry_for_game_key(
        store, game_id,
    )
    exe = (entry.exe if entry else "") or ""
    work_dir = (entry.work_dir if entry else "") or ""

    # Repair a stale/missing games.map row by re-resolving the exe from
    # the install dir. Games installed by older builds (or discovered
    # via manifest) never had an exe resolved, so their row is absent
    # (e.g. Amazon) or carries an empty exe (e.g. GOG ``start.sh``).
    # Re-resolving here lets already-installed games launch without a
    # reinstall. The ``exe="xcloud"`` sentinel is a valid target and
    # skips this; Microsoft is xCloud-only and is handled below.
    if not exe and store != "microsoft":
        if not work_dir:
            work_dir = _install_path_from_cache(store, game_id)
        resolved = (
            _resolve_exe_from_install(store, work_dir) if work_dir else None
        )
        if resolved:
            logger.info(
                "[launcher.dispatcher] re-resolved exe for %s → %s",
                game_key, resolved,
            )
            exe = resolved

    if not exe:
        # Microsoft titles are Xbox Cloud Gaming (browser-streamed) —
        # no install and no games.map row needed. Synthesize the xCloud
        # context so the dispatch matrix routes to ``_launch_xcloud``
        # (which builds the stream URL from ``game_id``).
        if store == "microsoft":
            logger.info(
                "[launcher.dispatcher] microsoft game not in games.map — "
                "synthesizing xCloud context for %s", game_key,
            )
            return LaunchContext(
                store=store,
                game_id=game_id,
                exe_path=Path("xcloud"),
                work_dir=_resolve_plugin_dir(),
                plugin_dir=_resolve_plugin_dir(),
                raw_options=raw_options,
                is_launch_action=True,
                auth_store=None,
                bypass_circuit_breaker=False,
            )
        raise GameNotFoundError(
            f"game {game_key!r} not found in games.map",
            context={"game_key": game_key},
        )

    bypass = _resolve_bypass_flag(store, game_id)
    return LaunchContext(
        store=store,
        game_id=game_id,
        exe_path=Path(exe),
        work_dir=Path(work_dir) if work_dir else _resolve_plugin_dir(),
        plugin_dir=_resolve_plugin_dir(),
        raw_options=raw_options,
        is_launch_action=True,
        auth_store=None,
        bypass_circuit_breaker=bypass,
    )

def _detect_auth_action() -> tuple[str | None, bool]:

    """Detect auth action."""
    auth_env = {
        "epic":      os.environ.get("UNIFIDECK_EPIC_ACTION"),
        "gog":       os.environ.get("UNIFIDECK_GOG_ACTION"),
        "amazon":    os.environ.get("UNIFIDECK_AMAZON_ACTION"),
        "microsoft": os.environ.get("UNIFIDECK_MICROSOFT_ACTION"),
        "ubisoft":   os.environ.get("UNIFIDECK_UBISOFT_ACTION"),
    }
    for candidate_store, action in auth_env.items():
        if action == "auth":
            return candidate_store, False
    return None, True
def _resolve_bypass_flag(store: str, game_id: str) -> bool:
    """Resolve bypass flag."""
    bypass_raw = os.environ.get(
        "UNIFIDECK_BYPASS_CIRCUIT_BREAKER", "",
    )
    bypass_env = bypass_raw.strip().lower() in (
        "1", "true", "yes",
    )
    try:
        from unifideck.config.config_manager import ConfigManager
        from unifideck.services.launch_history import LaunchHistoryService
        cfg = ConfigManager(
            str(
                _resolve_plugin_dir()
                / "defaults" / "config.json",
            ),
        )
        lh = LaunchHistoryService(cfg)
        bypass_flag = lh.consume_bypass(f"{store}:{game_id}")
    except Exception:
        bypass_flag = False
    return bypass_env or bypass_flag
async def _run(argv: list[str]) -> int:
    """Run."""
    from .diagnostics.correlation import launch_id_scope, new_launch_id
    from .diagnostics.log_archive import (
        attach_launch_handler,
        detach_launch_handler,
        prune_old_launches,
    )
    lid = new_launch_id()
    with launch_id_scope(lid):
        try:
            from unifideck.config.config_manager import ConfigManager
            from unifideck.core.paths import resolve_plugin_dir
            _cfg = ConfigManager(str(
                resolve_plugin_dir() /
                "defaults" /
                "config.json"))
        except Exception:
            _cfg = None
        prune_old_launches(_cfg)
        _archive_handler = attach_launch_handler(lid, _cfg)
        try:
            return await _run_with_id(argv)
        finally:
            detach_launch_handler(_archive_handler)

async def _run_with_id(argv: list[str]) -> int:

    """Run with ID."""
    try:
        game_key, _ = _parse_argv(argv)
        logger.info(
            "[launcher.dispatcher] request received: %s", game_key,
        )
    except LauncherError as err:
        logger.exception(
            "[launcher.dispatcher] argv parse failed: %s",
            err.to_log_dict(),
        )
        return int(err.exit_code)
    try:
        launcher_service = _bootstrap_minimal_services()
    except Exception:
        logger.exception("[launcher.dispatcher] bootstrap failed")
        return int(ExitCode.DEPENDENCY_MISSING)
    try:
        ctx = await _build_context(argv, launcher_service._shortcut_svc)
    except LauncherError as err:
        logger.exception(
            "[launcher.dispatcher] context build failed: %s",
            err.to_log_dict(),
        )
        return int(err.exit_code)
    try:
        await launcher_service.start()
        result = await launcher_service.launch(ctx)
    except LauncherError as err:
        logger.exception(
            "[launcher.dispatcher] launch raised: %s",
            err.to_log_dict(),
        )
        return int(err.exit_code)
    finally:
        try:
            await launcher_service.stop()
        except Exception:
            logger.exception(
                "[launcher.dispatcher] launcher_service.stop failed",
            )
    return _map_result_to_exitcode(result)
def _map_result_to_exitcode(result: Result) -> int:
    """Map result to exitcode."""
    if result.success:
        return int(ExitCode.SUCCESS)
    code = result.error_code or ""
    if code == "not_implemented":
        return int(ExitCode.GENERIC_ERROR)
    if code == "circuit_open":
        return int(ExitCode.CIRCUIT_BREAKER_OPEN)
    if code.startswith("exit_"):
        try:
            rc = int(code.split("_", 1)[1])
            return rc if 0 <= rc <= 255 else int(ExitCode.GAME_FAILED)
        except (ValueError, IndexError):
            return int(ExitCode.GAME_FAILED)
    return int(ExitCode.GAME_FAILED)
def _bootstrap_minimal_services() -> Any:
    """Bootstrap minimal services.

    Returns the ``LauncherService`` built by
    ``launcher.bootstrap.build_launcher_service``. Typed as
    ``Any`` to match the source return type (the bootstrap
    helper itself is intentionally untyped at the call site to
    avoid circular imports).
    """
    from .bootstrap import build_launcher_service
    return build_launcher_service()

def main(argv: list[str]) -> int:

    """Main."""
    from .diagnostics.correlation import install_launch_id_logging
    logging.basicConfig(
        format=(
            "%(asctime)s [%(launch_id)s] %(levelname)s "
            "%(name)s: %(message)s"
        ),
        level=logging.INFO,
        stream=sys.stderr,
    )
    install_launch_id_logging()
    try:
        return int(asyncio.run(_run(argv)))
    except KeyboardInterrupt:
        return int(ExitCode.CANCELLED_BY_USER)
    except asyncio.CancelledError:
        logger.info(
            "[launcher.dispatcher] launch cancelled by user",
        )
        return int(ExitCode.CANCELLED_BY_USER)
    except Exception:
        logger.exception("[launcher.dispatcher] uncaught exception")
        return int(ExitCode.GENERIC_ERROR)
if __name__ == "__main__":
    sys.exit(main(sys.argv))
