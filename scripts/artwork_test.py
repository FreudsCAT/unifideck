#!/usr/bin/env python3
"""Standalone artwork pipeline tester.

Runs outside the Decky plugin runtime — bypasses the EventBus,
SyncService, ShortcutService and every other Layer-5 service.
Just reads ``shortcuts.vdf`` directly, identifies Unifideck-managed
entries by ``LaunchOptions`` pattern, and exercises the artwork
pipeline against each one. Output is verbose by design so we can
see exactly which source resolved each kind and which files actually
landed on disk.

Usage:

    python3 scripts/artwork_test.py [--dry-run] [--limit N]
                                   [--shortcuts PATH] [--grid PATH]

Iterations:

* **v1** — enumerate Unifideck-managed entries via LaunchOptions regex.
* **v2** — exercise the artwork pipeline (per-store metadata + SGDB
  + Steam CDN) per game, download to ``grid/``, verify writes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Any

# Make ``unifideck`` importable when run directly from the plugin
# checkout, so we can reuse the production fetcher / VDF reader.
_HERE = Path(__file__).resolve()
_PLUGIN_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "py_modules"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("artwork_test")


STORE_ID_PATTERN = re.compile(
    r"\b(epic|gog|amazon|ubisoft|microsoft):"
    r"([a-zA-Z0-9][a-zA-Z0-9._-]*)",
)


def find_steam_paths() -> tuple[Path | None, Path | None]:
    """Return ``(shortcuts_vdf, grid_dir)`` for the most-recent user."""
    candidates = (
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
    )
    for root in candidates:
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        best: tuple[float, str] | None = None
        for entry in userdata.iterdir():
            if not entry.is_dir() or entry.name in ("0", "anonymous", "ac"):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, entry.name)
        if best is None:
            continue
        user_dir = userdata / best[1] / "config"
        return user_dir / "shortcuts.vdf", user_dir / "grid"
    return None, None


def read_vdf(path: Path) -> dict[str, Any]:
    """Read shortcuts.vdf via the plugin's persistence helper."""
    from unifideck.services.shortcut.persistence import read_vdf

    # ``persistence.read_vdf`` is async; we're synchronous here.
    return asyncio.run(read_vdf(str(path)))


def write_vdf(path: Path, data: dict[str, Any]) -> None:
    """Write shortcuts.vdf via the plugin's persistence helper."""
    from unifideck.services.shortcut.persistence import write_vdf

    asyncio.run(write_vdf(str(path), data))


def list_unifideck_shortcuts(
    vdf: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return parsed Unifideck-managed entries from the vdf payload."""
    shortcuts = vdf.get("shortcuts", {})
    if not isinstance(shortcuts, dict):
        return []
    out: list[dict[str, Any]] = []
    for ord_key, entry in shortcuts.items():
        if not isinstance(entry, dict):
            continue
        # ``LaunchOptions`` is the canonical Unifideck signature.
        launch = entry.get("LaunchOptions", "")
        if not isinstance(launch, str):
            continue
        m = STORE_ID_PATTERN.search(launch)
        if not m:
            continue
        store, game_id = m.group(1), m.group(2)
        if f"{store}:{game_id}" == "ubisoft:upc-auth":
            continue
        appid = entry.get("appid")
        out.append({
            "ord": ord_key,
            "appid_signed": appid,
            "appid_unsigned": (
                appid + 0x100000000 if isinstance(appid, int) and appid < 0
                else appid
            ),
            "title": entry.get("AppName", ""),
            "store": store,
            "game_id": game_id,
            "current_icon": entry.get("icon", ""),
        })
    return out


def summarise_grid(grid: Path, appid_unsigned: int) -> dict[str, bool]:
    """Inspect ``grid/`` for the five canonical artwork files."""
    if not isinstance(appid_unsigned, int):
        return {}
    files = {
        "grid":   grid / f"{appid_unsigned}p.jpg",
        "grid_pn": grid / f"{appid_unsigned}p.png",
        "grid_l": grid / f"{appid_unsigned}.jpg",
        "hero":   grid / f"{appid_unsigned}_hero.jpg",
        "logo":   grid / f"{appid_unsigned}_logo.png",
        "icon":   grid / f"{appid_unsigned}_icon.jpg",
    }
    return {k: p.exists() for k, p in files.items()}


def report_enumeration(
    shortcuts_path: Path, grid: Path, entries: list[dict[str, Any]], limit: int,
) -> None:
    print("\n=== Enumeration ===")
    print(f"shortcuts.vdf: {shortcuts_path}")
    print(f"grid/        : {grid}  (exists={grid.exists()})")
    print(f"Unifideck-managed entries: {len(entries)}")
    print()
    by_store: dict[str, int] = {}
    for e in entries:
        by_store[e["store"]] = by_store.get(e["store"], 0) + 1
    for store, count in sorted(by_store.items()):
        print(f"  {store:>10}: {count}")
    print()
    if not entries:
        return
    print(f"--- First {min(limit, len(entries))} entries (with disk state) ---")
    for e in entries[:limit]:
        files = summarise_grid(grid, e["appid_unsigned"])
        has = [k for k, ok in files.items() if ok]
        missing = [k for k in ("grid", "hero", "logo", "icon") if not files.get(k)]
        print(
            f"  [{e['ord']:>4}] "
            f"signed={e['appid_signed']:>11} unsigned={e['appid_unsigned']:>10} "
            f"{e['store']:>8} :: {e['title']}"
        )
        print(f"          have={has or '-'}  missing={missing or '-'}")


_SGDB_KEY = "1a410cb7c288b8f21016c2df4c81df74"
_DOWNLOAD_TIMEOUT = 30


_KINDS = ("grid", "grid_l", "hero", "logo", "icon")


async def _phase_store(
    store: str, game_id: str, appid_unsigned: int, grid: Path,
    result: dict[str, bool], sources: dict[str, str], title: str,
) -> None:
    from unifideck.services.artwork.fetcher import download_and_save
    from unifideck.services.artwork.store_metadata import fetch_store_urls

    try:
        store_urls = await fetch_store_urls(store, game_id)
    except Exception as e:
        logger.debug("[%s] store metadata failed: %s", title, e)
        return
    if store in ("gog", "amazon"):
        store_urls.pop("logo", None)
    store_urls.pop("icon", None)
    for kind, url in store_urls.items():
        if kind not in _KINDS or result.get(kind) or not url:
            continue
        if await download_and_save(
            str(grid), appid_unsigned, kind, url, _DOWNLOAD_TIMEOUT,
        ):
            result[kind] = True
            sources[kind] = store.upper()


async def _phase_sgdb(
    title: str, appid_unsigned: int, grid: Path, sgdb_key: str,
    result: dict[str, bool], sources: dict[str, str],
) -> None:
    from unifideck.services.artwork.fetcher import download_and_save, find_artwork_url

    for kind in _KINDS:
        if result.get(kind):
            continue
        sgdb_kind = "grid" if kind == "grid_l" else kind
        try:
            url = await find_artwork_url(title, sgdb_kind, sgdb_key, None)
        except Exception:
            url = None
        if not url:
            continue
        if await download_and_save(
            str(grid), appid_unsigned, kind, url, _DOWNLOAD_TIMEOUT,
        ):
            result[kind] = True
            sources[kind] = "SGDB"


async def _phase_steam(
    title: str, appid_unsigned: int, grid: Path,
    result: dict[str, bool], sources: dict[str, str],
) -> None:
    from unifideck.services.artwork.fetcher import download_and_save
    from unifideck.services.artwork.store_metadata import steam_cdn_urls, steam_search_appid

    try:
        steam_id = await steam_search_appid(title)
    except Exception:
        steam_id = None
    if not steam_id:
        return
    for kind, url in steam_cdn_urls(steam_id).items():
        if kind not in _KINDS or result.get(kind):
            continue
        if await download_and_save(
            str(grid), appid_unsigned, kind, url, _DOWNLOAD_TIMEOUT,
        ):
            result[kind] = True
            sources[kind] = "STEAM"


async def fetch_one(
    entry: dict[str, Any], grid: Path, sgdb_key: str,
    *, sem: asyncio.Semaphore,
) -> dict[str, str]:
    """Run the staging-style pipeline for one shortcut, return per-kind source map."""
    from unifideck.services.artwork.fetcher import has_artwork

    appid_unsigned = entry["appid_unsigned"]
    if not isinstance(appid_unsigned, int) or appid_unsigned <= 0:
        return {}
    title = entry["title"]
    if await has_artwork(str(grid), appid_unsigned):
        logger.info("[%s] already has art", title)
        return {"skipped": "already-present"}
    sources: dict[str, str] = {}
    result = dict.fromkeys(_KINDS, False)
    async with sem:
        await _phase_store(
            entry["store"], entry["game_id"], appid_unsigned, grid,
            result, sources, title,
        )
        await _phase_sgdb(title, appid_unsigned, grid, sgdb_key, result, sources)
        if not all(result.values()):
            await _phase_steam(title, appid_unsigned, grid, result, sources)
    return sources


async def run_pipeline(
    entries: list[dict[str, Any]], grid: Path, limit: int,
    *, only_missing: bool, concurrency: int,
) -> dict[str, Any]:
    """Iterate entries, fetch artwork concurrently, gather a summary."""
    if only_missing:
        entries = [
            e for e in entries
            if not all(
                summarise_grid(grid, e["appid_unsigned"]).get(k)
                for k in ("grid", "hero", "logo", "icon")
            )
        ]
    entries = entries[:limit]
    sem = asyncio.Semaphore(concurrency)

    async def _one(e: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        srcs = await fetch_one(e, grid, _SGDB_KEY, sem=sem)
        return e, srcs

    results = await asyncio.gather(*[_one(e) for e in entries])
    summary: dict[str, Any] = {
        "total": len(entries),
        "by_kind": dict.fromkeys(("grid", "grid_l", "hero", "logo", "icon"), 0),
        "by_source": {},
        "failures": [],
    }
    for e, srcs in results:
        if not srcs:
            summary["failures"].append((e["title"], "all sources empty"))
            continue
        if srcs.get("skipped"):
            continue
        for kind, src in srcs.items():
            summary["by_kind"][kind] += 1
            summary["by_source"][src] = summary["by_source"].get(src, 0) + 1
    return summary


def report_pipeline(summary: dict[str, Any]) -> None:
    print()
    print("=== Pipeline summary ===")
    print(f"games processed : {summary['total']}")
    print(f"by kind         : {summary['by_kind']}")
    print(f"by source       : {summary['by_source']}")
    if summary["failures"]:
        print(f"failures        : {len(summary['failures'])}")
        for title, why in summary["failures"][:10]:
            print(f"  - {title}: {why}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shortcuts", type=Path,
                    help="Override path to shortcuts.vdf")
    ap.add_argument("--grid", type=Path,
                    help="Override path to grid/ directory")
    ap.add_argument("--limit", type=int, default=20,
                    help="Max games to process")
    ap.add_argument("--enumerate", action="store_true",
                    help="v1 mode: just list entries, no downloads")
    ap.add_argument("--all", action="store_true",
                    help="Process every Unifideck shortcut (overrides --limit)")
    ap.add_argument("--include-existing", action="store_true",
                    help="Re-fetch games that already have art on disk")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="Parallel downloads cap (default 4)")
    args = ap.parse_args()

    auto_shortcuts, auto_grid = find_steam_paths()
    shortcuts_path = args.shortcuts or auto_shortcuts
    grid = args.grid or auto_grid
    if shortcuts_path is None or grid is None:
        print("ERROR: could not auto-locate Steam paths; pass --shortcuts/--grid", file=sys.stderr)
        return 2
    if not shortcuts_path.exists():
        print(f"ERROR: shortcuts.vdf not found at {shortcuts_path}", file=sys.stderr)
        return 2
    grid.mkdir(parents=True, exist_ok=True)

    vdf = read_vdf(shortcuts_path)
    entries = list_unifideck_shortcuts(vdf)
    report_enumeration(shortcuts_path, grid, entries, args.limit)

    if args.enumerate:
        return 0

    limit = len(entries) if args.all else args.limit
    summary = asyncio.run(run_pipeline(
        entries, grid, limit,
        only_missing=not args.include_existing,
        concurrency=args.concurrency,
    ))
    report_pipeline(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
