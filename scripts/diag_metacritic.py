#!/usr/bin/env python3
"""Read-only diagnostic for the non-Steam Metacritic-at-sync bug.

Symptom: many shortcuts have no Metacritic when sorting the Steam
library, fall into "Everything Else", but opening details / resyncing
fixes them. This script measures, against the *live on-disk caches*,
how many shortcuts the per-shortcut facet would surface a Metacritic
for, and splits the misses into:

  * ASSIGN  — a score IS cached (``metadata[store:game_id]``) but the
              facet can't reach it (the facet joins via ``steam_appid``,
              which the backfill never stamps), and
  * FETCH   — no score cached at all (sync-time appdetails fetch was
              rate-limited / backfill didn't cover it).

Nothing is mutated. Optionally (``--fetch N``) it re-fetches a sample
of FETCH-gap shortcuts straight from Steam's appdetails endpoint (with
429 backoff) to confirm a score exists at the source.

Usage:
    python3 scripts/diag_metacritic.py
    python3 scripts/diag_metacritic.py --fetch 10
    python3 scripts/diag_metacritic.py --cache-dir /path/to/cache
"""

from __future__ import annotations

import argparse
import binascii
import json
import re
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = "/home/deck/homebrew/data/Unifideck/cache"
DEFAULT_LAUNCHER = "/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher"
SHORTCUTS_GLOB = "/home/deck/.steam/steam/userdata/*/config/shortcuts.vdf"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"


def load_cache(cache_dir: str, ns: str) -> dict[str, Any]:
    """Load one ``<ns>_cache.json`` namespace's ``data`` dict (or {})."""
    path = Path(cache_dir) / f"{ns}_cache.json"
    try:
        return json.loads(path.read_text()).get("data", {})
    except OSError as exc:
        print(f"  (could not load {ns}: {exc})")
        return {}


def find_launcher_path() -> str:
    """Best-effort: read the launcher Exe from shortcuts.vdf, else default.

    All Unifideck shortcuts share one ``Exe`` (the launcher); it's the
    ``launcher`` half of ``generate_app_id(launcher, "store:game_id")``.
    """
    for vdf in Path("/").glob(SHORTCUTS_GLOB.lstrip("/")):
        try:
            blob = vdf.read_bytes()
        except OSError:
            continue
        m = re.search(rb"([^\x00\"]*unifideck-launcher)", blob)
        if m:
            return m.group(1).decode("utf-8", "replace")
    return DEFAULT_LAUNCHER


def gen_app_id(launcher: str, identity: str) -> int:
    """Mirror ``services/shortcut/games_map.generate_app_id`` (signed 32-bit)."""
    crc = binascii.crc32(f"{launcher}|{identity}".encode()) | 0x80000000
    return crc - 0x100000000 if crc > 0x7FFFFFFF else crc


def _steam_metacritic(steam_meta: dict[str, Any]) -> int | None:
    """Metacritic embedded in Steam's appdetails payload (if any)."""
    mc = steam_meta.get("metacritic") if isinstance(steam_meta, dict) else None
    if isinstance(mc, dict) and isinstance(mc.get("score"), int):
        return mc["score"]
    return None


def current_facet_metacritic(
    steam_meta: dict[str, Any],
    composite_mc: dict[int, int],
    real_id: int,
) -> int | None:
    """What the facet surfaces TODAY: appdetails, else the composite map
    keyed by ``steam_appid`` (drops scores whose entry lacks one)."""
    return _steam_metacritic(steam_meta) or composite_mc.get(real_id)


def fixed_facet_metacritic(
    steam_meta: dict[str, Any],
    store_gid_entry: dict[str, Any] | None,
) -> int | None:
    """What the FIXED facet surfaces: appdetails, else the robust native
    ``metadata[store:game_id]`` score (no ``steam_appid`` dependency)."""
    score = _steam_metacritic(steam_meta)
    if score is not None:
        return score
    if isinstance(store_gid_entry, dict):
        s = store_gid_entry.get("metacritic_score")
        if isinstance(s, int) and s > 0:
            return s
    return None


def fetch_score(steam_app_id: int, ctx: ssl.SSLContext) -> int | None | str:
    """Direct appdetails fetch with simple 429 backoff. Returns score,
    None (no score), or an error string."""
    url = f"{APPDETAILS_URL}?appids={steam_app_id}&filters=basic,metacritic&cc=us&l=english"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
                d = json.load(r)
            node = d.get(str(steam_app_id), {}) or {}
            if not node.get("success"):
                return None
            mc = (node.get("data", {}) or {}).get("metacritic", {})
            return mc.get("score") if isinstance(mc, dict) else None
        except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
            if e.code == 429:
                time.sleep(2**attempt)
                continue
            return f"HTTP {e.code}"
        except (urllib.error.URLError, OSError, ValueError) as e:
            return str(e)
    return "429 (gave up)"


def _build_composite(meta: dict[str, Any]) -> dict[int, int]:
    """The CURRENT (broken) join: ``steam_appid`` → score, only from
    entries that carry a positive ``steam_appid``."""
    out: dict[int, int] = {}
    for e in meta.values():
        if not isinstance(e, dict):
            continue
        s = e.get("metacritic_score")
        if not (isinstance(s, int) and s > 0):
            continue
        try:
            sid = int(e.get("steam_appid") or 0)
        except (TypeError, ValueError):
            sid = 0
        if sid > 0:
            out.setdefault(sid, s)
    return out


def _build_real_to_entry(
    meta: dict[str, Any],
    real: dict[str, Any],
    launcher: str,
) -> dict[int, dict[str, Any]]:
    """Map real Steam AppID → its ``metadata[store:game_id]`` entry, via
    ``generate_app_id`` (the FIXED join's native key)."""
    out: dict[int, dict[str, Any]] = {}
    for key, entry in meta.items():
        rid = real.get(str(gen_app_id(launcher, key)))
        if isinstance(rid, int) and rid > 0 and isinstance(entry, dict):
            out[rid] = entry
    return out


def _classify(
    real: dict[str, Any],
    smeta: dict[str, Any],
    real_to_entry: dict[int, dict[str, Any]],
    composite_mc: dict[int, int],
    fetch_sample: int,
) -> dict[str, Any]:
    """Split unique shortcuts into current-has / fixed-has / assign
    (recovered by the store:gid join) / fetch (no cached score)."""
    current_has = fixed_has = assign = fetch = 0
    samples_assign: list[str] = []
    samples_fetch: list[tuple[int, str]] = []
    seen: set[int] = set()
    for rid in (v for v in real.values() if isinstance(v, int) and v > 0):
        if rid in seen:
            continue
        seen.add(rid)
        sm = smeta.get(str(rid)) or {}
        entry = real_to_entry.get(rid)
        cur = current_facet_metacritic(sm, composite_mc, rid)
        fix = fixed_facet_metacritic(sm, entry)
        if cur is not None:
            current_has += 1
        if fix is None:
            fetch += 1
            if len(samples_fetch) < max(10, fetch_sample):
                samples_fetch.append((rid, str(sm.get("name", "?"))[:34]))
            continue
        fixed_has += 1
        if cur is None:  # recovered purely by the store:gid join
            assign += 1
            if len(samples_assign) < 10:
                samples_assign.append(
                    f"{str((entry or {}).get('title', '?'))[:34]:34} score={fix} real={rid}",
                )
    return {
        "total": len(seen),
        "current_has": current_has,
        "fixed_has": fixed_has,
        "assign": assign,
        "fetch": fetch,
        "samples_assign": samples_assign,
        "samples_fetch": samples_fetch,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    ap.add_argument("--launcher", default=None, help="override launcher path")
    ap.add_argument(
        "--fetch",
        type=int,
        default=0,
        help="direct-fetch N FETCH-gap shortcuts to confirm source scores",
    )
    args = ap.parse_args()

    launcher = args.launcher or find_launcher_path()
    real = load_cache(args.cache_dir, "steam_real_appid")  # shortcut_id -> real
    smeta = load_cache(args.cache_dir, "steam_metadata")  # real_id -> appdetails
    meta = load_cache(args.cache_dir, "metadata")  # store:gid -> entry

    print(f"launcher: {launcher}")
    print(
        f"caches: steam_real_appid={len(real)} steam_metadata={len(smeta)} metadata={len(meta)}",
    )

    real_to_entry = _build_real_to_entry(meta, real, launcher)
    composite_mc = _build_composite(meta)
    scored = sum(
        1
        for e in meta.values()
        if isinstance(e, dict)
        and isinstance(e.get("metacritic_score"), int)
        and e["metacritic_score"] > 0
    )
    res = _classify(real, smeta, real_to_entry, composite_mc, args.fetch)

    print(f"\nmetadata entries with a metacritic score: {scored}")
    print(f"\nshortcuts (unique real appid): {res['total']}")
    print(f"  facet HAS metacritic NOW (steam_appid join):   {res['current_has']}")
    print(f"  facet HAS metacritic FIXED (store:gid join):   {res['fixed_has']}")
    print(f"  -> ASSIGN recovered by the fix (no re-fetch):  {res['assign']}")
    print(f"  facet None, FETCH (no cached score anywhere):  {res['fetch']}")

    print("\n=== sample ASSIGN (recovered by Part 1, no re-fetch) ===")
    for s in res["samples_assign"]:
        print(f"  {s}")

    if args.fetch:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        print(f"\n=== direct appdetails fetch ({args.fetch} FETCH-gap) ===")
        for rid, name in res["samples_fetch"][: args.fetch]:
            print(f"  {rid:>8} {name:34} source score={fetch_score(rid, ctx)}")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
