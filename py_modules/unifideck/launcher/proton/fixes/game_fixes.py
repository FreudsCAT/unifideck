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
# ── Rockstar-on-Epic games (RDR2 / GTA5) ──────────────────────────
# These Epic titles boot the Rockstar Games Launcher, which then runs
# the real game exe (``PlayRDR2.exe`` / ``PlayGTAV.exe``). Getting them
# to launch under Proton/umu needs a bundle of Rockstar-specific
# handling that would REGRESS ordinary Epic titles if applied globally:
#   * STORE=egs (not "none") so umu's protonfixes apply the egs profile;
#   * WINEDLLOVERRIDES=vulkan-1=n,b (the documented RDR2 launch fix);
#   * KEEP the ``com.epicgames.launcher`` registry handler (epic_cleanup
#     strips it for every other Epic game) + a fake EpicGamesLauncher.exe
#     stub beside the game exe, so the game's ``start EpicGamesLauncher.exe
#     PlayRDR2.exe`` handoff resolves without the real Epic launcher.
# Every one of those is gated behind :func:`is_rockstar_egs` so a normal
# Epic launch is byte-for-byte unchanged. Online play never works
# (BattlEye has no Linux support); this is story-mode only.
#
# PRIMARY key = the Epic **app name** (legendary's ``game_id``, e.g.
# "Heather"): it's the one identifier ALWAYS present at launch. The
# umu-database ``umu_id`` is NOT reliable here — it's resolved via the
# optional ``bin/umu_lookup.py`` helper which is not bundled in the Decky
# build, so ``core.proton_prepare`` reports ``umu_id=None`` for every game
# (the reason the first cut of this flow never fired for RDR2 — the gate
# was keyed on umu_id). The umu id is kept as a SECONDARY match only, for
# the case where the lookup helper is present and returns one.
ROCKSTAR_EGS_APP_NAMES: frozenset[str] = frozenset({
    "Heather",                          # Red Dead Redemption 2 (Epic codename)
    "9d2d0eb64d5c44529cece33fe2a46482",  # Grand Theft Auto V (Epic, legacy)
    "8769e24080ea413b8ebca3f1b8c50951",  # Grand Theft Auto V: Enhanced Edition (Epic)
})
ROCKSTAR_EGS_UMU_IDS: frozenset[str] = frozenset({
    "umu-1174180",  # Red Dead Redemption 2
    "umu-271590",   # Grand Theft Auto V
})
# The game's own Play-launcher exe, relative to the install dir, that the
# Rockstar bootstrap ultimately runs. Used as the ``--override-exe`` target
# so legendary launches it directly rather than the Epic-launcher stub.
# Keyed by BOTH the Epic app name and the umu id so either identity resolves.
ROCKSTAR_PLAY_EXES: dict[str, str] = {
    "Heather": "PlayRDR2.exe",
    "umu-1174180": "PlayRDR2.exe",
    "9d2d0eb64d5c44529cece33fe2a46482": "PlayGTAV.exe",
    "8769e24080ea413b8ebca3f1b8c50951": "PlayGTAV.exe",
    "umu-271590": "PlayGTAV.exe",
}
# vulkan-1=n,b = native-then-builtin: lets the game's own vulkan-1.dll
# load first. Heroic's documented RDR2/GTA5 fix for the launch failure.
ROCKSTAR_WINEDLLOVERRIDES = "vulkan-1=n,b"
# THIRD, most-durable match tier: the Rockstar Play-launcher exe name itself
# (lowercased). Rockstar/Take-Two re-release these titles under a NEW Epic
# app id every edition — legacy "Grand Theft Auto V"
# (9d2d0eb64d5c44529cece33fe2a46482) and the 2026 "Enhanced Edition"
# (8769e24080ea413b8ebca3f1b8c50951) are two different ids for what a user
# experiences as "the same game" — and ROCKSTAR_EGS_APP_NAMES above silently
# stops matching every time that happens.
#
# That is exactly what broke the Enhanced Edition (reported as: the Rockstar
# Games Launcher doesn't detect the installed game). Confirmed from a user
# log bundle that contains a clean A/B of both titles on one device:
#   * RDR2 ("Heather", in the allowlist) — 10 launches, each logging the full
#     flow: fake launcher installed, protocol registered, STORE=egs,
#     WINEDLLOVERRIDES+=vulkan-1=n,b.
#   * GTA V Enhanced (id absent from the allowlist) — 12 launches, NONE of
#     the above; the game log shows protonfixes falling back to
#     "No store specified, using UMU database" instead of the egs profile.
# So this title received none of the Rockstar handling at all.
#
# NOTE: the reporter also described it working on the first boot and failing
# after a restart. The per-boot mechanism for that is NOT established — the
# obvious suspect, epic_cleanup stripping the registration on later launches,
# is NOT supported by those logs (it never logged a removal for this game,
# and it only logs when it actually removes something). Treat the
# first-boot/restart asymmetry as unexplained; what IS proven is that the
# whole flow was skipped for this title.
#
# The Play-launcher exe names are a Rockstar Games Launcher contract that is
# edition-independent, so this is checked FIRST, ahead of the id allowlists.
ROCKSTAR_PLAY_EXE_NAMES: frozenset[str] = frozenset({
    "playrdr2.exe",
    "playgtav.exe",
})


def is_rockstar_egs(
    game_id: str | None,
    umu_id: str | None = None,
    exe_name: str | None = None,
) -> bool:
    """True for the Rockstar-on-Epic titles that need the special flow.

    Three tiers, most durable first: ``exe_name`` (the game's own exe
    filename, e.g. "PlayGTAV.exe" — stable across every Epic-catalog
    edition/re-release of the title, so prefer this everywhere the caller
    has it); ``game_id``, legendary's Epic app name (e.g. "Heather") — the
    reliable identifier ALWAYS present at launch, but a new one is minted
    every time Rockstar reshuffles their Epic listings; ``umu_id`` — an
    optional secondary match (the umu-database id, only populated when
    ``bin/umu_lookup.py`` is present). Any one match returns True; matching
    none returns False so the ordinary Epic path is taken unchanged.
    """
    if exe_name and exe_name.lower() in ROCKSTAR_PLAY_EXE_NAMES:
        return True
    if game_id and game_id in ROCKSTAR_EGS_APP_NAMES:
        return True
    return bool(umu_id) and umu_id in ROCKSTAR_EGS_UMU_IDS


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
