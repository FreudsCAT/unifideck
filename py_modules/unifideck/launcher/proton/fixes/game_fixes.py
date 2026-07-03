from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, cast

logger = logging.getLogger(__name__)
@dataclass(frozen=True)
class GameFix:
    """Game fix."""
    winetricks: list[str] = field(default_factory=list)
    exe_override: str | None = None
    notes: str = ""
    source: str = ""

GLOBAL_DEFAULTS: list[str] = [
    "vcrun2005",
    "vcrun2008",
    "vcrun2010",
    "vcrun2012",
    "vcrun2013",
    "vcrun2022",
    "d3dcompiler_47",
    "d3dcompiler_43",
    "mfc140",
]
MANUAL_FIXES: dict[str, GameFix] = {
    "Dodo": GameFix(
        winetricks=[],
        notes="Works with Proton + EOS only",
        source="manual",
    ),
    "ea8df71f923649a193ab1c1fded7e1b3": GameFix(
        winetricks=[
            "vcrun2005", "vcrun2008", "vcrun2010", "vcrun2012",
            "vcrun2013", "vcrun2022",
        ],
        exe_override=(
            "Ghostrunner/Binaries/Win64/"
            "Ghostrunner-Win64-Shipping.exe"
        ),
        notes=(
            "UE4 stub bypassed — launches shipping binary "
            "directly. The default Ghostrunner.exe is a 540KB "
            "launcher stub that probes VC++ runtime registry "
            "keys via MsiQueryProductState and shows a "
            "'Microsoft Visual C++ Runtime' error even when "
            "DLLs are present. Proton rewrites system.reg at "
            "launch time, making registry injection impossible."
        ),
        source="manual",
    ),
    "fa5aa7e6c28c4c94aeac239eee700d5f": GameFix(
        winetricks=[],
        notes="EOS overlay only, no redistributables needed",
        source="manual",
    ),
}
_UMU_DATABASE_URL_FORMATS = [
    "https://raw.githubusercontent.com/Open-Wine-Components/"
    "umu-database/main/umu-egs-{game_id}.json",
    "https://raw.githubusercontent.com/Open-Wine-Components/"
    "umu-database/main/umu-epic-{game_id}.json",
]
_UMU_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_CACHE_TTL_SECONDS = 3600
def _user_exe_override(game_id: str) -> str | None:
    """The user's "Change executable" choice (``games.<id>.executable``).

    Read from the live user config so Epic's legendary ``--override-exe`` honors
    a UI-set launch executable (the direct-launch stores use the games.map exe
    column instead). Relative to the install dir, matching the curated
    ``MANUAL_FIXES`` ``exe_override`` shape. Best-effort; never raises.
    """
    try:
        from unifideck.launcher.bootstrap import _load_standalone_config
        val = _load_standalone_config().get(f"games.{game_id}.executable")
        return str(val) if val else None
    except Exception:
        return None


def get_exe_override(game_id: str) -> str | None:
    """Resolve the launch-exe override (relative path) for a game.

    The user's "Change executable" choice wins; otherwise the curated
    ``MANUAL_FIXES`` table. ``None`` when neither applies.
    """
    user = _user_exe_override(game_id)
    if user:
        return user
    fix = MANUAL_FIXES.get(game_id)
    if fix is None:
        return None
    return fix.exe_override

async def fetch_umu_protonfixes(game_id: str) -> dict[str, Any] | None:

    """Fetch UMU protonfixes."""
    now = time.monotonic()
    cached = _UMU_CACHE.get(game_id)
    if (
        cached is not None
        and now - cached[0] < _CACHE_TTL_SECONDS
    ):
        return cached[1]
    _UMU_CACHE[game_id] = (now, None)
    try:
        import aiohttp
    except ImportError:
        logger.info(
            "[game_fixes] aiohttp not available, skipping "
            "umu-database lookup for %s", game_id,
        )
        return None
    timeout = aiohttp.ClientTimeout(total=10)
    # ssl=False — SteamOS's outdated cert store breaks strict TLS verification
    # for the umu-database host, same as every other HTTP path in the plugin.
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for url_format in _UMU_DATABASE_URL_FORMATS:
            url = url_format.format(game_id=game_id)
            data = await _try_umu_url(session, url)
            if data is not None:
                logger.info(
                    "[game_fixes] found umu-db "
                    "entry for %s", game_id,
                )
                _UMU_CACHE[game_id] = (now, data)
                return cast("dict[Any, Any] | None", data)
    logger.info(
        "[game_fixes] no umu-db entry for %s (expected "
        "for most games)", game_id,
    )
    return None
async def _try_umu_url(
    session: Any, url: str,
) -> dict[str, Any] | None:
    """Try UMU URL."""
    import aiohttp
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
            return cast("dict[Any, Any] | None", data)
    except (aiohttp.ClientError, json.JSONDecodeError) as e:
        logger.debug(
            "[game_fixes] %s lookup failed: %s", url, e,
        )
        return None
async def get_required_winetricks(game_id: str) -> list[str]:
    """Get required winetricks."""
    manual = MANUAL_FIXES.get(game_id)
    if manual is not None:
        logger.info(
            "[game_fixes] manual override for %s: %s",
            game_id, manual.winetricks,
        )
        return list(manual.winetricks)
    umu_data = await fetch_umu_protonfixes(game_id)
    if umu_data and isinstance(
        umu_data.get("winetricks"), list,
    ):
        packages = umu_data["winetricks"]
        logger.info(
            "[game_fixes] umu-db for %s: %s",
            game_id, packages,
        )
        return list(packages)
    logger.info(
        "[game_fixes] global defaults for %s", game_id,
    )
    return list(GLOBAL_DEFAULTS)

async def get_game_fix(game_id: str) -> GameFix:

    """Get game fix."""
    manual = MANUAL_FIXES.get(game_id)
    if manual is not None:
        return manual
    umu_data = await fetch_umu_protonfixes(game_id)
    if umu_data:
        return GameFix(
            winetricks=list(
                umu_data.get("winetricks") or [],
            ),
            exe_override=umu_data.get("exe_override"),
            notes=str(umu_data.get("notes") or ""),
            source="umu-protonfixes",
        )
    return GameFix(
        winetricks=list(GLOBAL_DEFAULTS),
        notes=(
            "Using global defaults "
            "(vcrun*, d3dcompiler, mfc140)"
        ),
        source="global_default",
    )
def clear_cache() -> None:
    """Clear cache."""
    _UMU_CACHE.clear()
