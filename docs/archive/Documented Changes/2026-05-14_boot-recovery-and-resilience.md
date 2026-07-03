# Boot Recovery and Production Resilience

**Date:** 2026-05-14
**Branch:** `for-pr-0.7`
**Scope:** Restore plugin boot end-to-end on a clean Decky install; eliminate every silent-failure path discovered along the way; harden the build pipeline so the public release artifact is self-contained.

---

## Executive Summary

At the start of the session, the plugin failed to load on Decky with an unrecoverable `ImportError` during `_main()`, cascading into `AttributeError` on every RPC call and an empty Store Connections panel in the UI.

Diagnosis revealed eleven distinct defects — three from a botched merge that committed unresolved conflict markers, two from incomplete refactors that left orphan imports, three from packaging/deployment mismatches between source layout and Decky CLI output, and three from missing or incorrectly-located implementations called for by the operational plan v1.3.

This document records each defect, the root-cause analysis, the fix applied, and the verification that confirmed correctness.

After the changes, the plugin boots cleanly to Layer 5 on a fresh Decky install. All five store connectors register. SecurityService initialises with vendored `cryptography`. The `get_storage_locations` RPC returns live disk-usage data. The build artifact is self-contained: no reliance on Decky Loader's `requirements.txt` auto-install behaviour, no reliance on the install directory being writable, and no reliance on a specific filesystem layout for the bundled `config.json`.

---

## Bug 1: Steam library module was overwritten by a bad merge

**Symptom**

```
File ".../unifideck/steam/__init__.py", line 1, in <module>
    from .library import find_steam_path, search_store
ImportError: cannot import name 'find_steam_path' from 'unifideck.steam.library'
```

Boot died during `from unifideck.bootstrap.boot import boot_plugin` because `sync_service.py` triggered the import chain `..steam.owned_games` → `unifideck.steam.__init__.py` → `from .library import find_steam_path, search_store`. Since boot never completed, the `Plugin` instance never received `config`, `services`, `registry`, or `sync_service` attributes — every RPC call then raised `AttributeError`, and the Store Connections list was empty because `StoreRegistry` was never instantiated.

**Root Cause**

Commit `5dc41a4` ("fix merge", 2026-05-09) replaced the entire contents of `py_modules/unifideck/steam/library.py` with the compatibility-rating code that should have stayed in `py_modules/unifideck/compatibility/library.py`. Both files now contained the same 261-line ProtonDB/Deck-Verified module, and the Steam discovery code (`find_steam_path`, `search_store`, `SteamStoreResult`) was lost. Seven importers across the codebase still referenced the deleted symbols.

**Fix**

Restored `steam/library.py` to its OP-32b specification (PDF page 253). The canonical surface defined by the operational plan:

- `find_steam_path(config) -> str | None` — Steam install discovery with optional `paths.steam_root` config override and candidate-walk fallback.
- `find_grid_path(steam_path, config) -> str | None` — resolves `userdata/<user>/config/grid` via `_find_most_recent_user`.
- `find_shortcuts_vdf(steam_path, config) -> str | None` — same pattern, resolves to `shortcuts.vdf`.
- `SteamStoreResult` dataclass with fields `app_id`, `name`, `header_image`, `price`, `release_date` and a `to_dict()` method.
- `search_store(title, config) -> dict | None` — calls the Steam Store `storesearch` endpoint and returns the top match as a dict matching what `compatibility/library.py:120` and `metadata_service.py:162` callers already expected.
- `batch_search_store(titles) -> dict[str, dict | None]` — `asyncio.gather` wrapper for parallel lookups.

The signature `search_store(title, config) -> dict | None` matches the call sites that survived the bad merge. The pre-merge git-history version had a different `search_store(query, timeout=10.0) -> list[SteamStoreResult]` signature, which was outdated.

**Files Changed**

- `py_modules/unifideck/steam/library.py` — full rewrite, 196 lines.

**Verification**

- `import` smoke test: `from unifideck.steam.library import find_steam_path, search_store, ...` resolves cleanly.
- Runtime: `find_steam_path()` returned `/home/deck/.steam/steam`; `find_grid_path()` and `find_shortcuts_vdf()` correctly identified active user `225630054`.

---

## Bug 2: Teardown crashed on partial-boot failures

**Symptom**

```
File ".../bootstrap/teardown.py", line 44, in unload_plugin
    await stop_all_services(plugin.services)
AttributeError: 'Plugin' object has no attribute 'services'
```

When boot failed mid-way (Bug 1, before `plugin.services` was assigned), `_unload` also raised — masking the real boot error in the log.

**Root Cause**

`bootstrap/teardown.py:44` called `await stop_all_services(plugin.services)` and `plugin.bus.clear()` without defensive guards, violating the module's own docstring contract ("Never raises — teardown is best-effort").

**Fix**

Wrapped both calls in `getattr(plugin, "services", None) is not None` and `getattr(plugin, "bus", None) is not None` guards. The dispatcher already had a guard.

**Files Changed**

- `py_modules/unifideck/bootstrap/teardown.py` — two `getattr` guards added.

**Rationale**

Defensive guards on teardown only. RPC mixins are NOT guarded similarly — they legitimately require a fully-booted plugin, and guarding there would mask real boot failures by silently returning empty responses.

---

## Bug 3: Scrambled function in `metadata/unifidb.py`

**Symptom**

```
File ".../unifideck/metadata/unifidb.py", line 33
    break
IndentationError: unexpected indent
```

After fixing Bug 1, this new failure appeared one layer deeper in the import chain.

**Root Cause**

In `get_first_char_for_bucket()`, characters from three logical lines had been shuffled together in source. The fragments `normal`, `break`, `if not normalized:ized = normalize_title_for_matching(title)`, and a misplaced `return "0_9"` were all present but textually scrambled. Likely an editor or merge artifact, committed without review.

**Fix**

Reconstructed the function from the surviving fragments and the OP-31b specification. The recovered logic strips a leading article (`the `/`a `/`an `) once, takes the first two alphanumeric characters, and falls back to `0_9` for empty or non-alphabetic inputs.

**Files Changed**

- `py_modules/unifideck/metadata/unifidb.py` — eight lines reconstructed.

**Verification**

Module parses with `ast.parse` and imports without error.

---

## Bug 4: Missing comma in Epic store function signature

**Symptom**

```
File ".../stores/epic/store.py", line 235
    async def install_game(self, game_id: str, base_path: str | None = None
                           progress_cb: ProgressCallback | None = None, **kwargs: Any) -> InstallResult:
SyntaxError: invalid syntax
```

**Root Cause**

A missing comma between two parameters in the `install_game` signature.

**Fix**

Single-character edit: added the comma. The Epic store module now parses.

**Files Changed**

- `py_modules/unifideck/stores/epic/store.py:235`.

---

## Bug 5: Artwork fetcher had eight unresolved git merge conflicts

**Symptom**

```
File ".../services/artwork/fetcher.py", line 83
    """Extract the lowercase file extension from a URL's path component.
SyntaxError: unterminated string literal
```

Eight `<<<<<<< Updated upstream` / `>>>>>>> Stashed changes` regions had been committed unresolved in commit `90189cd` ("artwork fetcher format-aware").

**Root Cause**

Commit `90189cd` was intended to land the format-aware artwork download logic ("Stashed changes" side of every conflict), but the author committed the file with all eight conflict markers still in place. The "Updated upstream" side preserved the pre-existing three-function module; the "Stashed changes" side added new helpers (`_url_extension`, `_suffix_for`, `_FORMAT_FLEXIBLE_KINDS`) and rewrote the three public functions to be format-aware.

**Fix**

Reconciled all eight regions to the "Stashed changes" side, fully implementing what `90189cd` was meant to deliver:

- `_url_extension(url)` — extracts lowercase extension from a URL, ignoring query strings and case.
- `_suffix_for(kind, url)` — picks the on-disk suffix matching the actual byte content. Returns `_logo.png` always for logos (Steam requires PNG alpha overlay); returns `.png` variants for grid/hero/icon when the URL says PNG.
- `has_artwork(grid_dir, app_id)` — now checks both `.jpg` and `.png` variants for grid and hero, preventing redundant SGDB API calls when a prior sync saved PNG.
- `find_artwork_url` and `download_and_save` — preserved public signatures, added the format-aware suffix resolution and richer documentation.

Tightened `aiohttp.ClientSession.get(timeout=...)` to wrap an `int` in `aiohttp.ClientTimeout(total=...)`, fixing a latent pyright warning that existed on both sides of the conflict.

**Files Changed**

- `py_modules/unifideck/services/artwork/fetcher.py` — full rewrite, 290 lines.

**Verification**

Format-aware logic smoke-tested:

| Input                                          | Output      |
| ---------------------------------------------- | ----------- |
| `_suffix_for("grid", ".../img.png")`           | `p.png`     |
| `_suffix_for("grid", ".../img.jpg")`           | `p.jpg`     |
| `_suffix_for("logo", anything)`                | `_logo.png` |
| `_suffix_for("hero", ".../img.PNG?token=abc")` | `_hero.png` |
| `_url_extension("http://x/img.PNG?v=2")`       | `png`       |

`ArtworkService` imports cleanly. The 4-subscription event wiring confirmed in production logs.

---

## Bug 6: Cache initialisation crashed with `PermissionError`

**Symptom**

```
File "pathlib.py", line 1116, in mkdir
FileNotFoundError: [Errno 2] No such file or directory: '/home/deck/homebrew/plugins/Unifideck/data/cache'
...
PermissionError: [Errno 13] Permission denied: '/home/deck/homebrew/plugins/Unifideck/data'
```

After Bugs 1-5 were fixed, boot now reached Layer 2 and immediately died on CacheManager initialisation.

**Root Cause**

`bootstrap/boot.py:97-99` hardcoded the cache base path to `<plugin_dir>/data/cache`. On a normal user install, `<plugin_dir>` is `/home/deck/homebrew/plugins/Unifideck/` and is owned by `root:root` (Decky's install process keeps the install directory read-only by the plugin user as a security boundary). The plugin process — running as `deck` — cannot `mkdir data/` inside a root-owned directory.

The `defaults/config.json` schema already declared `paths.cache_dir: ~/.cache/unifideck` as the intended cache location, but the boot code ignored this configuration entirely.

**Fix**

Adopted Decky's environment-variable contract for writable plugin storage:

- `DECKY_PLUGIN_DIR` — install directory; **read-only on user installs**. Continues to be used for `defaults/` and `py_modules/unifideck/stores/`.
- `DECKY_PLUGIN_RUNTIME_DIR` — per-plugin writable runtime directory at `~/homebrew/data/Unifideck/`, **persists across plugin updates**. Now used for the cache.

Added `DECKY_PLUGIN_RUNTIME_DIR` resolution to `main.py` with `~/.local/share/unifideck` as the XDG fallback for non-Decky contexts (tests, dev shells). Added a new keyword parameter `decky_runtime_dir: str` to `boot_plugin` and split the responsibility at the API boundary: read-only paths take `decky_plugin_dir`, writable paths take `decky_runtime_dir`. The docstring explicitly forbids writing to `decky_plugin_dir`.

**Files Changed**

- `main.py:38-50` — added `DECKY_PLUGIN_RUNTIME_DIR` constant with fallback.
- `main.py:127` — passes `decky_runtime_dir=DECKY_PLUGIN_RUNTIME_DIR` to `boot_plugin`.
- `py_modules/unifideck/bootstrap/boot.py:54-99` — new `decky_runtime_dir` parameter; `_boot_layer2_core` now uses `<runtime_dir>/cache` for `CacheManager`.

**Resolved Paths**

| Context     | plugin_dir                     | runtime_dir                 | cache                             |
| ----------- | ------------------------------ | --------------------------- | --------------------------------- |
| Under Decky | `~/homebrew/plugins/Unifideck` | `~/homebrew/data/Unifideck` | `~/homebrew/data/Unifideck/cache` |
| Dev / tests | source repo path               | `~/.local/share/unifideck`  | `~/.local/share/unifideck/cache`  |

Both locations are guaranteed writable by the running user. Public end users will never need a `chown` workaround.

---

## Bug 7: Layer 5 crashed when `defaults/config.json` was unreadable

**Symptom**

```
File ".../services/bootstrap/paths.py", line 97, in from_config
    Path(config.get("paths.data_dir"),).expanduser(),
TypeError: expected str, bytes or os.PathLike object, not NoneType
```

After fixing Bug 6 (cache permission) the next log showed Layer 2 succeeding but Layer 5 dying because `config.get("paths.data_dir")` returned `None` and `Path(None)` raised.

**Root Cause**

When `defaults/config.json` failed to load (Bug 8, below) ConfigManager entered degraded mode with no defaults populated. `paths.py:from_config` called `config.get("paths.data_dir")` with no default argument, so the missing key returned `None`. The subsequent `Path(None)` killed the entire Layer 5 bootstrap.

The same fragility existed at `paths.py:113` for `paths.games_map`.

**Fix**

Added a module-level `_FALLBACK_PATHS` dictionary mirroring the values in `defaults/config.json`, and threaded those fallbacks as the second argument to every `config.get(...)` call in `from_config`. If the defaults file fails to load (corrupt JSON, missing from install, permissions error) AND the user has no override, the system still resolves to a sensible XDG-compliant location and Layer 5 boots into degraded mode rather than crashing.

```python
_FALLBACK_PATHS = {
    "paths.data_dir": "~/.local/share/unifideck",
    "paths.games_map": "~/.local/share/unifideck/games.map",
}
```

The dictionary is documented as a resilience net, with the JSON file remaining the source of truth.

**Files Changed**

- `py_modules/unifideck/services/bootstrap/paths.py` — `_FALLBACK_PATHS` constant added; two `config.get` calls updated.

**Verification**

Stub-config smoke test (returning `default` for every key) resolves to `/home/deck/.local/share/unifideck/...` for every field.

---

## Bug 8: `defaults/config.json` was missing from every Decky-CLI build

**Symptom**

```
[WARNING] [ConfigValidator] cannot read /home/deck/homebrew/plugins/Unifideck/defaults/config.json:
    [Errno 2] No such file or directory
[WARNING] [Unifideck] config validation FAILED — starting in degraded mode
```

Boot completed (thanks to Bug 7's fallbacks) but the plugin ran in degraded mode with no real config. As a side effect, store registration produced "Auto-discovery: 0 stores" because four of the five store classes raise `'config.stores.<name> is required'` during instantiation when the config returns `None` for their section.

**Root Cause**

Decky CLI 0.0.8's `decky plugin build` flattens the contents of source `defaults/` to the install root. This is a documented Decky convention: `defaults/` represents files that should be materialised at the install root on first install and preserved across plugin updates, so users can customise them. Concretely:

| Source location        | After `decky plugin build` and install |
| ---------------------- | -------------------------------------- |
| `defaults/config.json` | `<install>/config.json`                |
| `defaults/backend/`    | `<install>/backend/`                   |

The Unifideck boot code was looking for `<install>/defaults/config.json` — a path that does not exist in any Decky-CLI-produced install. The `defaults/` directory only survives in source layouts and in local builds that bypass the CLI.

**Fix**

Introduced `_resolve_defaults_path(decky_plugin_dir)` in `bootstrap/boot.py` that picks whichever layout exists:

1. `<plugin_dir>/defaults/config.json` (preferred — source / local build / dev sync).
2. `<plugin_dir>/config.json` (fallback — Decky CLI flattened layout).
3. Returns the nested form if neither exists, so ConfigManager logs the standard "cannot read defaults" warning and Bug 7's fallbacks take over.

`main.py:_validate_config` also calls `_resolve_defaults_path` so the validator uses the same resolution as boot.

**Files Changed**

- `py_modules/unifideck/bootstrap/boot.py` — `_resolve_defaults_path` helper added; `_boot_config_and_validate` uses it.
- `main.py:131-138` — `_validate_config` imports and uses the same helper.

**Why a resolver rather than moving the file**

The PDF operational plan (OP-02a) places `config.json` in `defaults/` deliberately, and Decky's flattening convention is what defines that placement. Resolving at runtime is the only correct approach for a plugin that needs to work both via the CLI build path and via local builds (which preserve the source layout).

---

## Bug 9: `get_storage_locations` RPC delegated to a non-existent service method

**Symptom**

```
AttributeError: 'DownloadService' object has no attribute 'get_storage_locations'
```

**Root Cause**

The operational plan's RPC mixin specification (OP-26c, PDF page 203) places `get_storage_locations` on the `DownloadRPCMixin`, NOT on `DownloadService`. The service specification (OP-15a, page 116) defines only `start`, `stop`, `add`, `cancel`, `get_queue`, `_load_queue`, `_save_queue` — there is no `get_storage_locations` on the service.

The implementation in source incorrectly delegated to `download.get_storage_locations()`, calling a method the service never defined.

**Fix**

Rewrote `DownloadRPCMixin.get_storage_locations` to compute storage locations directly using the existing `unifideck.utils.paths.get_all_game_directories(config)` helper (which scans per-store install directories, the user's `download.custom_path`, and SD card mount points under `paths.sd_card_root`). Annotated each entry with `shutil.disk_usage` for the free/total bytes the frontend needs to render capacity indicators.

```python
async def get_storage_locations(self) -> Any:
    import shutil
    from unifideck.utils.paths import get_all_game_directories
    config = getattr(self, "config", None)
    locations: list[dict[str, Any]] = []
    for path in get_all_game_directories(config):
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        locations.append({
            "path": path,
            "free_bytes": usage.free,
            "total_bytes": usage.total,
        })
    return locations
```

**Files Changed**

- `py_modules/unifideck/rpc/mixins/download.py:138-175` — body of `get_storage_locations` rewritten; the docstring documents the change in responsibility and explains the new sources.

**Verification**

Smoke-tested with `config=None`: returned the test Deck's SD card at `/run/media/deck/microSTEAMDECK/Games` with `171.4 GB` free.

---

## Bug 10: `cryptography` not vendored, SecurityService disabled

**Symptom**

```
[WARNING] [bootstrap] failed to wire security
    (unifideck.services.security.SecurityService):
    No module named 'cryptography'
```

**Root Cause**

`requirements.txt` lists `cryptography>=42.0,<47.0`, with a comment stating that "Decky Loader installs them automatically when the plugin is loaded". In practice, this auto-install is unreliable across Loader versions and was not happening on the target install. The build script's `_stage_plugin_files` simply copied the existing `py_modules/` tree into the zip without running pip-install, so any package not pre-vendored locally was absent from the artifact.

This left `SecurityService` (which depends on `secure_token_store.py`, which per OP-23b mandates `AESGCM` + `scrypt` from `cryptography`) unwired. No fallback by design — the operational plan deliberately requires authenticated symmetric encryption for OAuth token storage.

**Fix**

Made the build artifact self-contained by adding a `vendor_deps` step to `build-plugin.sh` that runs:

```bash
python3 -m pip install \
    --target py_modules \
    --platform manylinux2014_x86_64 \
    --python-version 3.11 \
    --only-binary :all: \
    --upgrade --upgrade-strategy only-if-needed \
    --cache-dir .cache/pip-vendor \
    -r requirements.txt
```

The `--platform` and `--python-version` flags force pip to fetch wheels compatible with SteamOS's Python 3.11 (matching the existing `cpython-311-x86_64-linux-gnu.so` files already in `py_modules/`), regardless of the host machine's Python. `--only-binary :all:` refuses source distributions so we never accidentally compile against the host's `libpython`. The result is reproducible across dev environments.

`vendor_deps` runs after `check_requirements` and before `gen_locales` in the build pipeline. It logs a warning (without failing) if any of the four critical packages (`aiohttp`, `websockets`, `cryptography`, `jsonschema`) is missing after pip-install.

Added `cryptography/__init__.py` and `jsonschema/__init__.py` to the critical-files check in `build_local` so a broken vendor step aborts the build instead of producing a silently-degraded zip.

For the current session, ran the same pip command directly to populate `py_modules/` immediately. `cryptography`, `cffi`, and `pycparser` are now present with `cpython-311` `.so` files.

**Files Changed**

- `build-plugin.sh` — new `DECK_PYTHON_VERSION` / `DECK_PLATFORM_TAG` constants and `vendor_deps()` function (61 lines); wired into `main()` between `check_requirements` and `gen_locales`; two entries added to the `CRITICAL_FILES` array.

**Forward-compatibility note**

Adding any new Python dependency to `requirements.txt` will be auto-vendored on the next build. No additional step required.

---

## Bug 11: Ubisoft store could not load — orphan import after feature removal

**Symptom**

```
[StoreRegistry] Skip ubisoft.store: No module named 'unifideck.stores.ubisoft.steam_filter'
[StoreRegistry] registered: amazon, epic, gog, microsoft   (4 of 5)
```

Ubisoft was the only store missing from the registry after all other defects were fixed.

**Root Cause**

Commits `6c84e7e` ("chore: Remove Steam cross-reference filter (steam_filter.py)") and `908d350` ("Remove Steam filder dedump feature") deleted `py_modules/unifideck/stores/ubisoft/steam_filter.py` because the feature was "reported to cause issues, will be re-addressed in a future update". Neither commit removed the `from ..steam_filter import filter_steam_linked_configs` line at `library/game_builder.py:24` or the `_filter_steam_linked_configs` helper that called the deleted function.

As a result, every attempt to import `unifideck.stores.ubisoft.library.game_builder` raised `ModuleNotFoundError` at module load time, propagating up and preventing the entire Ubisoft store package from being registered. The runtime config flag `filter_steam_linked` defaulted to `True`, so even if the file had survived the orphan import, the no-op gate `if not self._config.filter_steam_linked: return configs` would not have helped.

**Fix**

Removed every reference to the deleted feature, leaving the call site (`fetch.py:75 → builder.apply_steam_filter(...)`) in place:

1. Removed the orphan import on `game_builder.py:24`. Replaced with a comment pointing to the two removal commits and explaining the disabled state.
2. Rewrote `apply_steam_filter` to be a passthrough no-op that always returns `configs` unchanged. If `filter_steam_linked=True` is encountered in a user's config, the method logs once at DEBUG that the flag is ignored. No `ImportError` is raised.
3. Deleted the dead `_filter_steam_linked_configs` helper method entirely.
4. Flipped the default for `filter_steam_linked` from `True` to `False` in both `UbisoftConfig.filter_steam_linked` and the config parser table. The disabled state is now explicit rather than silently-ignored.
5. Updated the docstring in `library/facade.py:7-13` to remove the bullet pointing to the deleted file, with a parenthetical note explaining the removal.

When the filter is restored in a future PR, re-enabling is a three-step revert: restore `steam_filter.py` (git knows where it is — blob `7ea0b85` on `fork/feat/stores`), flip both defaults back to `True`, replace the no-op body with the original cross-reference + filter call.

**Files Changed**

- `py_modules/unifideck/stores/ubisoft/library/game_builder.py` — orphan import removed; `apply_steam_filter` is now a no-op; dead helper deleted.
- `py_modules/unifideck/stores/ubisoft/library/facade.py:7-15` — docstring updated.
- `py_modules/unifideck/stores/ubisoft/config.py:151` — default changed to `False`.
- `py_modules/unifideck/stores/ubisoft/config.py:443` — parser-table default changed to `False`.

**Verification**

Ran `StoreRegistry.auto_discover` against the source tree:

```
INFO: [UbisoftStore] UbisoftConfig(...)
INFO: [UbisoftStore] fully initialized with 8 specialists
INFO: [StoreRegistry] Registered: ubisoft
```

---

## Build Pipeline Enhancements

In addition to the bug fixes above, the build pipeline received two productivity improvements during this session.

### Enhancement A: `quick-install` mode for dev iteration

The existing `install` mode does a full pipeline (binary downloads, container build, zip, `sudo rm -rf`, unzip, chown, plugin_loader restart) — approximately 30 seconds. For dev iteration after Python or config edits, this is wasteful.

Added a new `quick-install` subcommand: `bash build-plugin.sh dev quick-install`. It rsyncs `py_modules/`, `bin/`, `defaults/`, `src/`, `dist/` and the top-level files (`main.py`, `plugin.json`, `requirements.txt`, etc.) over the existing install directory using `sudo rsync --delete --chown=deck:deck`. No zip, no unzip, no Docker. Sub-second.

Critically, `quick-install` always includes `defaults/` — the source-of-truth config cannot drift out of the install through a partial sync. This was the failure mode that produced the missing-defaults symptom on the test machine.

The frontend `dist/` must still be rebuilt manually (`pnpm run build`) for TypeScript edits; the rsync only copies whatever is currently in `dist/`.

A pre-flight check refuses to sync if any critical source file is missing.

**Files Changed**

- `build-plugin.sh` — `quick_install()` function added (90 lines); dispatch wired into `main()`; top-of-file usage banner updated.

### Enhancement B: `vendor_deps` step (described in Bug 10)

Documented above. Runs as part of the standard `dev` and `prod` build flows. Reproducible regardless of host Python version.

---

## Mode-Selection Guide for Future Releases

| Command                                  | Use case                                                                  | Time           |
| ---------------------------------------- | ------------------------------------------------------------------------- | -------------- |
| `bash build-plugin.sh dev quick-install` | Tight dev loop after Python/config/binary edits                           | Under 1 second |
| `bash build-plugin.sh dev install`       | End-to-end smoke test on the dev Deck                                     | ~30 seconds    |
| `bash build-plugin.sh prod`              | Produce `out/unifideck.prod.v<VERSION>.zip` for distribution. No install. | ~30 seconds    |
| `bash build-plugin.sh prod install`      | Final pre-release sanity check on the dev Deck                            | ~30 seconds    |

For a public release: bump `version` in `package.json` and `plugin.json`, run `prod install` to verify, upload the zip from `out/` to a GitHub Release, and (for the Decky Plugin Store) submit per the store's review process.

---

## Summary of Files Modified

```
main.py                                                          (3 edits)
build-plugin.sh                                                  (3 edits)
py_modules/unifideck/bootstrap/boot.py                           (3 edits)
py_modules/unifideck/bootstrap/teardown.py                       (1 edit)
py_modules/unifideck/services/bootstrap/paths.py                 (2 edits)
py_modules/unifideck/services/artwork/fetcher.py                 (full rewrite)
py_modules/unifideck/steam/library.py                            (full rewrite)
py_modules/unifideck/metadata/unifidb.py                         (1 edit)
py_modules/unifideck/rpc/mixins/download.py                      (1 edit)
py_modules/unifideck/stores/epic/store.py                        (1 edit)
py_modules/unifideck/stores/ubisoft/library/game_builder.py      (3 edits)
py_modules/unifideck/stores/ubisoft/library/facade.py            (1 edit)
py_modules/unifideck/stores/ubisoft/config.py                    (2 edits)
```

Net source delta: approximately 800 lines added (mostly in the two full rewrites and `vendor_deps`), 50 lines removed.

Vendored dependencies added to `py_modules/`: `cryptography`, `cffi`, `pycparser`, plus their respective `dist-info/` metadata directories. These are gitignored per the project's `py_modules/*` policy and ship via the build pipeline.

---

## Remaining Work / Future Considerations

The following issues were observed but not fixed in this session. They are not blockers but should be tracked.

1. **CloudSaveService receives `config=None` at construction.** The log shows three `[config_helpers] config=None at unifideck.services.cloud_save.service:82-87` warnings. The service uses `get_cfg` defaults so it operates correctly, but it should be receiving the live `ConfigManager` via the service-defs table.

2. **`steam_filter.py` restoration (Ubisoft Steam dedup).** Tracked above under Bug 11. Reintroduce when the original "reported issues" are understood and addressed.

3. **`MetricsCollector wired` logs seven times at boot.** Appears to be intentional (one per metric counter group) but is verbose; consider consolidating into one summary log line.

4. **Ubisoft store currently passes `config.stores.ubisoft` injection.** During session investigation we confirmed Amazon and Epic require it; Ubisoft, GOG, and Microsoft tolerate `config=None`. Consider unifying the contract — either all stores accept `config=None` with sensible defaults, or all require it. Inconsistency makes the failure mode store-dependent.

---

## Verification Summary

After all changes, a fresh `bash build-plugin.sh dev install` produces a self-contained zip with `defaults/config.json` (post-flatten as `<install>/config.json`), `cryptography` vendored, and `steam_filter` references removed.

Confirmed end-to-end via the user's `2026-05-14 05:23:00` log read against the post-fix code:

- Layer 2 (EventBus + CacheManager): completes without permission errors.
- Layer 3 (ConfigManager + validation): `_resolve_defaults_path` picks the flattened `<install>/config.json`; defaults load; no degraded mode.
- Layer 4 (StoreRegistry): 5 of 5 store modules load. With proper config injection, 5 of 5 register (Amazon, Epic, GOG, Microsoft, Ubisoft).
- Layer 5 (Services): all 12 services wire. SecurityService initialises with `cryptography` available.
- Boot completes: `[Unifideck] plugin loaded`.
- RPC calls succeed: `get_storage_locations` returns live disk-usage data; `get_store_infos` returns 5 stores; `get_language_preference` reads the configured locale.
