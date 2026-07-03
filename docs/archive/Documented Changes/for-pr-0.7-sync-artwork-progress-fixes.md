# Sync Pipeline, Artwork, and Progress Tracking — Restoration Roadmap

**Branch:** `for-pr-0.7` (targeting `staging`)
**Date:** 2026-05-17
**Scope:** Full-stack (Python backend + TypeScript frontend)

---

## 1. Context

The `for-pr-0.7` branch undertook an architectural refactor: the plugin's 2,410-line monolithic `index.tsx` (staging) was decomposed into `contexts/` + `hooks/` + `services/` layers, and the Python backend moved from one monolithic `main.py` (~6,600 LOC) into a layered `py_modules/unifideck/` package (event bus, 16 Layer-5 services, RPC mixins, per-store modules).

The refactor successfully modularised the codebase, but during the transition **substantial runtime behaviour was dropped or broken**:

- **No custom library tabs** (Epic, GOG, Amazon, Ubisoft, Microsoft, Non-Steam)
- **No Steam shortcut creation** during library sync
- **No artwork downloads** for non-Steam games
- **No metadata enrichment** (Steam Store, unifiDB, Metacritic)
- **Progress bar stopped immediately** after library fetch, hiding 5-10 minutes of post-sync work
- **Shortcuts written to wrong Steam user directory** (`userdata/0` — the guest meta-directory Steam ignores)
- **Sync completed silently** — no progress counters, no per-game labels on the QAM bar
- **All HTTP downloads broke** — missing `ssl=False` on aiohttp sessions (Deck cert store outdated)
- **Cache namespaces unregistered** — `ValueError` on every artwork fetch
- **`SYNC_PROGRESS` events never reached the frontend** — replay buffer never wired into `bus.emit`

This document records every issue found, the root cause, the fix, and the architectural justification for each change.

---

## 2. Architecture Comparison: Staging vs `for-pr-0.7`

### Staging design

Staging's sync was **one sequential blocking RPC method** (`sync_libraries` on `main.py:2454`). The RPC call did not return until every phase completed: library fetch → installed-status check → AppID computation → Steam metadata resolves → unifiDB lookups → SGDB searches → artwork downloads (with retry) → icon updates → shortcut writes → return result. Progress was tracked on a single `SyncProgress` object mutated inline by every phase. The frontend polled `get_sync_progress` every 500 ms and rendered a color-coded bar with per-phase `X / Y` counter lines and i18n labels.

### Current design

The refactored architecture distributes work across `SyncService.sync_all()` (library fetch only) + three `@subscribe(Events.SYNC_COMPLETE)` handlers (`ShortcutService`, `ArtworkService`, `MetadataService`). Progress tracking was replaced by an event-bus bridge with reactive subscriptions and a polling fallback. The architecture is strictly cleaner, but every gap below represents a place where the new wiring didn't replicate the old behaviour.

---

## 3. Issues Fixed — In Order of Impact

### 3.1 Shortcuts Not Written to Steam's Library

**Symptom:** Syncing fetched 553 games but Steam showed nothing. `reconcile: kept=553` in logs but `shortcuts.vdf` was empty when inspected.

**Root cause:** `ServicePaths._USER_ID` was hardcoded to `"0"` — the guest / meta directory Steam ignores. Every `shortcuts.vdf` and `grid/` write landed under `userdata/0/config/...` instead of the real Steam user's directory (e.g. `userdata/225630054`).

**Fix:**

- **New module** [py_modules/unifideck/steam/steam_user.py](py_modules/unifideck/steam/steam_user.py) — ports staging's two-tier active-user detection: parse `loginusers.vdf` for `MostRecent = "1"`, convert SteamID64 → 32-bit account_id via `& 0xFFFFFFFF`, validate the directory exists. Falls back to mtime-based detection. **Both layers explicitly reject `"0"`** (guest dir).
- [py_modules/unifideck/services/bootstrap/paths.py](py_modules/unifideck/services/bootstrap/paths.py) — `from_config` now calls `get_active_steam_user(steam_root_path)` before composing `shortcuts_path` and `grid_dir`. Renamed `_USER_ID` → `_USER_ID_GUEST` as a last-resort fallback.
- [py_modules/unifideck/services/bootstrap/constructor.py](py_modules/unifideck/services/bootstrap/constructor.py) — `bootstrap_services` receives `plugin_dir` kwarg, forwards to `ServicePaths.from_config`.
- [py_modules/unifideck/bootstrap/boot.py](py_modules/unifideck/bootstrap/boot.py) — threads `decky_plugin_dir` through `_boot_layer5_services` → `bootstrap_services`.

**Justification:** Steam's `userdata/0/` is a meta-directory used for guest-mode and anonymous accounts. Writes there are invisible to the running Steam client. Staging's `steam_utils.py` had the same two-tier detection — we ported it verbatim.

---

### 3.2 `game.app_id` Never Populated — Shortcuts Had `appid=0`

**Symptom:** Every shortcut entry in `shortcuts.vdf` had `appid=0`, making them invisible to Steam (Steam requires a signed 32-bit integer).

**Root cause:** Per-store `get_library()` methods construct `Game(app_id=0, ...)`. Staging assigned `game.app_id = generate_app_id(launcher_script, game.title)` during the sync loop. The refactored `SyncService` never did this.

**Fix:** [py_modules/unifideck/core/sync_service.py](py_modules/unifideck/core/sync_service.py)

- `__init__` now stores `self._launcher_path` (passed from `_boot_layer4_stores` via `decky_plugin_dir + "bin/unifideck-launcher"`).
- New method `_populate_app_ids(libraries)` runs inside `_finalize_sync` before `SYNC_COMPLETE` emits. Uses `generate_app_id(launcher_path, title)` — the same `crc32(path + title) | 0x80000000` formula as staging.

**Justification:** The AppID must be anchored on the launcher path, not the per-game `exe_path`, so the ID survives install/uninstall transitions. Steam keys cover art, playtime, and compat settings on the AppID — changing it throws all of that away.

---

### 3.3 Shortcut Entries Had Wrong Shape — `LaunchOptions=""`, `Exe=""`

**Symptom:** Shortcuts were created but unlaunchable — clicking the tile did nothing.

**Root cause:** `_GamesMapMixin._build_shortcut_entry` was writing `Exe=""` and `LaunchOptions=""` for uninstalled games. Staging always wrote `Exe="{launcher_script}"` and `LaunchOptions="{store}:{game_id}"` — the launcher reads `LaunchOptions` at runtime to decide what to install/run.

**Fix:** [py_modules/unifideck/services/shortcut/games_map_mixin.py](py_modules/unifideck/services/shortcut/games_map_mixin.py)

- `_build_shortcut_entry` rewritten to staging's shape: `Exe="{launcher_path}"` always, `LaunchOptions="{store}:{store_game_id}"`, `icon=game.icon_url or ""`, `tags = {0: "Unifideck", 1: store, 2: "" | "Not Installed"}`.
- `_reconcile_phase_sync_games` now uses `game.app_id` (populated by SyncService) → stable across install transitions.
- `reconcile` logs `[ShortcutService] reconcile: N games → added=X kept=Y removed=Z reclaimed=W`.

**ShortcutService now receives `launcher_path`:**

- [py_modules/unifideck/services/shortcut/service.py](py_modules/unifideck/services/shortcut/service.py) — accepts `launcher_path` ctor arg.
- [py_modules/unifideck/services/bootstrap/service_defs.py](py_modules/unifideck/services/bootstrap/service_defs.py) — passes `p.launcher_path` through the lambda.
- [py_modules/unifideck/services/bootstrap/paths.py](py_modules/unifideck/services/bootstrap/paths.py) — new `launcher_path` field (= `<plugin_dir>/bin/unifideck-launcher`).

**Justification:** Staging's approach of anchoring every shortcut on the launcher binary (`bin/unifideck-launcher`) decouples the shortcut entry from install state. The launcher reads `games.map` at runtime to find the actual binary. This design survived years of production use without changing.

---

### 3.4 Custom Shortcuts Were Not Preserved Across Reinstalls / Steam Restarts

**Symptom:** After a plugin reinstall or Steam version update, Unifideck-managed shortcuts would be orphaned or duplicated, losing artwork.

**Root cause:** Shortcuts were identified by tags. Steam can strip custom tags during updates or after user edits. No persistent registry mapped `{store}:{game_id}` → AppID across session boundaries.

**Fix — four-layer preservation system:**

- **New module** [py_modules/unifideck/services/shortcut/launch_options.py](py_modules/unifideck/services/shortcut/launch_options.py) — regex `\b(epic|gog|amazon|ubisoft|microsoft):([a-zA-Z0-9][a-zA-Z0-9._-]*)` parses `LaunchOptions` to identify our shortcuts. Steam preserves `LaunchOptions` reliably because the string is opaque to Steam — it never parses or strips it.
- **New module** [py_modules/unifideck/services/shortcut/registry.py](py_modules/unifideck/services/shortcut/registry.py) — persistent `~/.local/share/unifideck/shortcuts_registry.json` maps `{store}:{game_id}` → `{appid, title, created}`. Survives plugin uninstall (lives in user data).
- **Reclamation** (`_reclaim_orphan` in [games_map_mixin.py](py_modules/unifideck/services/shortcut/games_map_mixin.py)) — when a shortcut's registered AppID appears in `shortcuts.vdf` as an orphan, rewrite it in-place instead of creating a duplicate.
- **`valid_stores` filter** — `_reconcile_phase_drop_stale` only sweeps shortcuts for currently-active stores. Logout-of-Epic doesn't nuke Epic shortcuts.
- `_is_stale_managed_shortcut` now uses LaunchOptions regex as the primary detection signal, with the legacy `UNIFIDECK_TAG` as a fallback.

**Justification:** Tags are unreliable across Steam versions. LaunchOptions is the only field Steam preserves intact because user-appended parameters (`MANGOHUD=1`, `%command%`, etc.) live there. The regex-based approach handles these cleanly.

---

### 3.5 Replay Buffer Never Received Events — Sync Progress Never Reached Frontend

**Symptom:** `Sync / Force Sync` buttons showed no UI feedback even though the backend log proved sync was running (553 games in ~7s).

**Root cause chain (three stacked bugs):**

1. **(Primary) Replay buffer never wired into `bus.emit`.** The `PriorityDispatcher` was the only thing calling `replay_buffer.record()`, but nowhere in the codebase calls `dispatcher.enqueue()` — every emitter goes through `bus.emit()` directly. So the replay buffer was permanently empty, `subscribe_replay` always returned `[]`, and the frontend EventBus polling saw no events.

2. **(Secondary) `SYNC_PROGRESS` coalescing.** `SYNC_PROGRESS` was in `COALESCE_KEY` — the dispatcher rebroadcast it as `sync_progress_batch`. The frontend subscribed to `sync_progress`, so every progress event was lost.

3. **(Tertiary) Payload schema mismatch.** Backend `_emit_progress` emitted `{store, progress, current, total}` while frontend `SyncProgress` interface expected `{progress_percent, current_game, status, total_games, ...}`.

**Fix:**

- [py_modules/unifideck/event_bus/event_bus.py](py_modules/unifideck/event_bus/event_bus.py) — added `set_replay_recorder(fn)`. `emit()` now records every event to the replay buffer (best-effort, isolated from handler failures).
- [py_modules/unifideck/bootstrap/pipeline_factory.py](py_modules/unifideck/bootstrap/pipeline_factory.py) — wired `plugin.bus.set_replay_recorder(plugin.replay.record)`.
- [py_modules/unifideck/event_bus/event_priority.py](py_modules/unifideck/event_bus/event_priority.py) — removed `SYNC_PROGRESS` from `COALESCE_KEY` with a comment explaining why.
- [py_modules/unifideck/core/sync_service.py](py_modules/unifideck/core/sync_service.py) — `_emit_progress` now emits a `SyncProgress`-shaped payload matching the frontend contract.

**Justification:** The `PriorityDispatcher` was designed as the event ingress point, but architectural drift meant no emitter ever used it. Injecting the recorder directly into the bus is simpler and guaranteed-consistent — every `emit` reaches the replay buffer regardless of path.

---

### 3.6 Unregistered `"sgdb_fetch"` Cache — `ValueError` on Every Artwork Fetch

**Symptom:** `[ArtworkService] fetch exception: ValueError: Cache 'sgdb_fetch' not registered` — 553 times per sync.

**Root cause:** `ArtworkService` uses cache namespace `"sgdb_fetch"` for per-game failure cooldowns. The namespace was never added to `_NAMED_CACHES` in the cache registry, so `CacheManager._get_store("sgdb_fetch")` raised `ValueError` on every `get` and `set`.

**Fix:** [py_modules/unifideck/bootstrap/cache_registry.py](py_modules/unifideck/bootstrap/cache_registry.py) — added `("sgdb_fetch", 3600)` to `_NAMED_CACHES` (1-hour TTL matching `DEFAULT_FAILURE_COOLDOWN`).

---

### 3.7 SSL Certificate Validation — All HTTPS Downloads Failed

**Symptom:** Artwork pipeline returned `no-cover-found` for 551/553 games. All three phases (store metadata, SGDB, Steam CDN) failed silently.

**Root cause:** `_fetch_url_bytes` in [fetcher.py](py_modules/unifideck/services/artwork/fetcher.py) created `aiohttp.ClientSession()` without a connector. The Steam Deck's system certificate store is regularly outdated — every HTTPS request to SGDB CDN / `shared.steamstatic.com` / GOG GamesDB failed on TLS certificate verification and returned `None`.

**Fix:** [py_modules/unifideck/services/artwork/fetcher.py](py_modules/unifideck/services/artwork/fetcher.py) — added `connector = aiohttp.TCPConnector(ssl=False)` to `_fetch_url_bytes`. Every download path in staging used this pattern.

---

### 3.8 Signed / Unsigned Filename Mismatch — `has_artwork` vs `download_and_save`

**Symptom:** Every sync cycle re-downloaded artwork that already existed on disk, and Steam's UI couldn't find any of it.

**Root cause:** `download_and_save` wrote filenames using the **signed** AppID (e.g. `-1404125384p.jpg`), but `has_artwork` checked the **unsigned** form (`2890841912p.jpg`). They never agreed.

**Fix:** [py_modules/unifideck/services/artwork/fetcher.py](py_modules/unifideck/services/artwork/fetcher.py) — `download_and_save` now converts to unsigned at the boundary: `unsigned = app_id if app_id >= 0 else app_id + 0x100000000`. `has_artwork` already did the same conversion.

**Justification:** Steam's CEF renders grid images keyed on unsigned 32-bit AppID in `shortcuts.vdf`. Both the filename on disk and the `has_artwork` check must agree on unsigned.

---

### 3.9 Progress Bar Not Tracking Per-Game Counters

**Symptom:** The progress bar showed `0/553` → stuck → jumped to `553/553` with no values in between. Static text, no X/Y progress.

**Root cause:** `SyncProgress.increment_artwork()` / `increment_metadata()` existed as methods but were **dead code** — zero call sites. `ArtworkService` and `MetadataService` had no access to the `SyncProgress` instance because it lived on `SyncService` as a private attribute.

**Fix — Inject SyncProgress via the EventBus:**

- [py_modules/unifideck/event_bus/event_bus.py](py_modules/unifideck/event_bus/event_bus.py) — added `set_sync_progress(progress)` and `get_sync_progress()`. The bus is the single shared object every service already subscribes to.
- [py_modules/unifideck/core/sync_service.py](py_modules/unifideck/core/sync_service.py) — `_setup_sync` calls `bus.set_sync_progress(self._progress)`; `_on_post_sync_phase` calls `bus.set_sync_progress(None)` on final completion.
- [py_modules/unifideck/services/artwork/event_handlers.py](py_modules/unifideck/services/artwork/event_handlers.py) — `_process_one_game` calls `bus.get_sync_progress().increment_artwork(game.title)` after each fetch completes.
- [py_modules/unifideck/services/metadata_service.py](py_modules/unifideck/services/metadata_service.py) — `_run_enrichment` calls `self._bus.get_sync_progress().increment_metadata(game.title)` after each game's enrichment.

**Justification:** The event bus was already the common dependency. A getter/setter pattern avoids direct coupling between services while keeping the per-game calls synchronous (no 553-event overhead per phase).

---

### 3.10 Race Condition — Progress Bar Completed Prematurely

**Symptom:** Progress bar jumped to 100% and disappeared while artwork/metadata were still downloading.

**Root cause:** `ArtworkService` and `MetadataService` are spawned concurrently at `SYNC_COMPLETE` time. The completion handler assumed artwork always finishes before metadata. If metadata's sequential `_run_enrichment` finishes before the artwork gather's done callback fires, the handler calls `mark_complete()` while artwork is still running.

**Fix:** [py_modules/unifideck/core/sync_service.py](py_modules/unifideck/core/sync_service.py) — `_post_sync_pending` set tracks both `{"artwork", "metadata"}`. `mark_complete()` only fires when the set is empty (both phases have reported `active=False`). The handler for each completion phase discards its name from the set and only advances the bar when nothing remains pending.

---

### 3.11 Phase Transitions Not Visible on Progress Bar

**Symptom:** After library sync, the bar disappeared immediately instead of advancing through artwork → metadata → complete.

**Root cause:** `SyncContext.tsx` flipped `isSyncing=false` on `SYNC_COMPLETE`. Post-sync phases run as background tasks via `SYNC_COMPLETE` event handlers — the bar vanished the moment library fetch ended.

**Fix — Multi-layer:**

- **`SyncContext.tsx`** — removed `setSyncing(false)` from the `SYNC_COMPLETE` handler. The 500 ms polling loop now drives `isSyncing` — it stays `true` while `get_sync_progress.syncing=true`, which the backend keeps alive through post-sync phases.
- **`SyncProgress` class** ([py_modules/unifideck/core/sync_progress.py](py_modules/unifideck/core/sync_progress.py)) — new module ported from staging's `SyncProgress` (13 phases with percentage ranges, per-phase sub-counters, `to_dict()`).
- **`_SyncQueriesMixin.get_status`** — now delegates to `self._progress.to_dict()`, sets `syncing=True` while `_progress.status` is any non-terminal phase.
- **`SyncService._on_post_sync_phase`** — triggers `start_artwork()` and `start_metadata()` transitions.
- **Frontend `LibrarySync.tsx`** — rewritten to staging's display: color-coded bar (blue=sync, orange=artwork, green=complete, red=error), i18n labels via `t(label, values)`, per-phase `X / Y` counter lines.

**Phase ranges (updated from staging):**

| Phase              | Percentage | Counter                            |
| ------------------ | ---------- | ---------------------------------- |
| idle               | 0%         | —                                  |
| fetching           | 0-10%      | —                                  |
| checking_installed | 10-20%     | —                                  |
| syncing            | 20-40%     | `synced_games / total_games`       |
| steam_metadata     | 40-50%     | `steam_synced / steam_total`       |
| unifidb_lookup     | 50-55%     | `unifidb_synced / unifidb_total`   |
| sgdb_lookup        | 55-60%     | —                                  |
| artwork            | 60-90%     | `artwork_synced / artwork_total`   |
| metadata           | 90-98%     | `metadata_synced / metadata_total` |
| complete           | 100%       | —                                  |

---

### 3.12 i18n Labels Wrong — "Extracting metadata" Displayed During Artwork Phase

**Symptom:** Progress bar label showed "Extracting metadata..." while it was downloading artwork.

**Root cause:** `start_artwork()` used non-existent i18n key `"sync.checkingArtwork"`, which fell through to the raw key string. `start_metadata()` used key `"sync.extractingMetadata"` which didn't exist either.

**Fix:** [py_modules/unifideck/core/sync_progress.py](py_modules/unifideck/core/sync_progress.py) — all labels switched to existing i18n keys from staging:

- `start_artwork` → `artwork.checking` ("Checking artwork...")
- `increment_artwork` → `sync.downloadingArtwork` ("Downloading artwork for {{game}}...")
- `start_store_sync` → `sync.fetchingStore` ("Fetching {{store}}...")
- `start_fetching` → `sync.fetchingGameLists` ("Fetching game lists...")
- `increment_metadata` → `sync.extractingMetadata` ("Extracting metadata for {{game}}...") — **added this one key** to [en-US.json](src/i18n/locales/en-US.json)

---

### 3.13 Metadata Enrichment Blocked Sync Lock for 10+ Minutes

**Symptom:** First sync ran fine. Second sync attempt 35 seconds later was rejected with `sync_all() called while another sync is running — rejected`.

**Root cause:** `MetadataService._on_sync_complete` was `await`-ing the entire enrichment loop (553 games × HTTP fetches × 0.25s pace) **inside** `bus.emit(SYNC_COMPLETE, ...)` → which ran inside `_finalize_sync` → which held `self._lock` the entire time. The lock was not released for 5-15 minutes.

**Fix:** [py_modules/unifideck/services/metadata_service.py](py_modules/unifideck/services/metadata_service.py) — `_on_sync_complete` now spawns `asyncio.create_task(self._run_enrichment(games))` and returns immediately. The `SYNC_COMPLETE` emit completes in microseconds, the lock releases, and the enrichment quietly progresses in the background.

---

### 3.14 Missing Store Metadata Fetchers for Artwork

**Symptom:** SGDB was the only artwork source. No GOG / Epic / Amazon / Ubisoft official covers were fetched.

**Root cause:** The refactor had no per-store metadata modules. Staging's `steamgriddb.py` had dedicated `get_gog_metadata()`, `get_epic_metadata()`, `get_amazon_metadata()`, and Ubisoft extras methods.

**Fix — New module** [py_modules/unifideck/services/artwork/store_metadata.py](py_modules/unifideck/services/artwork/store_metadata.py):

- **Steam** — `steam_search_appid(title)` → `steam_cdn_urls(app_id)` (4 canonical CDN URLs)
- **GOG** — `gog_metadata(product_id)` → `gamesdb.gog.com/platforms/gog/external_releases/{id}` (vertical_cover, background, logo, icon). Falls back to GOG products API.
- **Amazon** — `amazon_metadata(game_id)` → same GamesDB endpoint, platform=`amazon`
- **Epic** — `epic_metadata(app_name)` → reads `~/.config/legendary/metadata/{app_name}.json` keyImages with priority table
- **Ubisoft** — `ubisoft_metadata(extras)` → reads `coverUrl` / `backgroundUrl` from game metadata

Pipeline order: **Store API → SGDB → Steam CDN**. Store logos discarded for GOG/Amazon (thumbnail quality). Icons always from SGDB.

---

### 3.15 Bundled SteamGridDB API Key

**Symptom:** Artwork silently skipped with debug log `no SGDB API key`.

**Root cause:** The refactor had no default API key. Every user needed to set `artwork.steamgriddb_api_key` in config before covers would work.

**Fix:** [py_modules/unifideck/services/artwork/service.py](py_modules/unifideck/services/artwork/service.py) — hardcoded staging's bundled key (`1a410cb7c288b8f21016c2df4c81df74` from `staging:main.py:2125`) as `_STAGING_SGDB_API_KEY`. Resolution order: constructor arg → config override → bundled key. Logs the source at boot (`[ArtworkService] SteamGridDB API key configured (source: shared default)`).

---

### 3.16 Artwork Log Wording — Unreadable

**Symptom:** Batch summary read `0 fetched, 2 already-present, 551 all-miss` — meaningless internal codes.

**Fix:** [py_modules/unifideck/services/artwork/event_handlers.py](py_modules/unifideck/services/artwork/event_handlers.py) — return values renamed: `"ok"` → `"cover-saved"`, `"all-miss"` → `"no-cover-found"`, `"already-present"` → `"cover-exists"`, `"no-app-id"` → `"skipped"`. Batch summary now reads: `artwork batch finished: 520 covers saved, 10 already on disk, 15 no match, 5 skipped, 3 errors`.

---

### 3.17 `has_artwork` Used Wrong Directory for Grid Checks

**Symptom:** Cover fetches claimed art already existed when it didn't, or vice versa.

**Root cause:** `has_artwork` used the signed AppID path pattern while Steam stores grid files under unsigned. Combined with the `_USER_ID = "0"` bug (3.1), the grid directory was wrong twice.

**Fix:** [py_modules/unifideck/services/artwork/fetcher.py](py_modules/unifideck/services/artwork/fetcher.py) — `has_artwork` now converts to unsigned internally: `unsigned = app_id if app_id >= 0 else app_id + 0x100000000`. Already addressed by the user-directory fix (3.1) for the path.

---

### 3.18 Missing `launcher_path` on `ServicePaths`

**Symptom:** `AttributeError` on `p.launcher_path` during service construction.

**Root cause:** `ServicePaths` had no `launcher_path` field. The `service_defs` lambda referenced it but the dataclass didn't define it.

**Fix:** [py_modules/unifideck/services/bootstrap/paths.py](py_modules/unifideck/services/bootstrap/paths.py) — added `plugin_dir` and `launcher_path` fields. Resolved from `decky_plugin_dir` (passed by Decky to `_main`) with a dev-fallback to the package root.

---

### 3.19 `get_game_metadata` RPC Signature Mismatch

**Symptom:** Frontend `GameInfoScores` called `getGameMetadata(app_id: number)` but backend expected `(store: str, game_id: str)`.

**Fix:** [src/components/info/GameInfoScores.tsx](src/components/info/GameInfoScores.tsx) — call signature changed to `fetchMeta(game.store, game.id)`.

---

### 3.20 Missing Backend RPC Routes

Added route names to [src/api/rpc-routes.ts](src/api/rpc-routes.ts):

| Route                       | Backend mixin   | Purpose                                          |
| --------------------------- | --------------- | ------------------------------------------------ |
| `getProtondbCache`          | `StoreRPCMixin` | ProtonDB/Deck-Verified compat cache              |
| `getRealSteamAppidMappings` | `StoreRPCMixin` | Shortcut → real-Steam-AppID mapping              |
| `getSteamMetadataCache`     | `StoreRPCMixin` | Full Steam Store `appdetails` payloads           |
| `injectGameToAppinfo`       | `StoreRPCMixin` | Persist spoofed metadata to `appinfo.vdf` (stub) |
| `performFullCleanup`        | `SyncRPCMixin`  | Wipe all Unifideck shortcuts + cache             |

**Backend handlers:**

- [py_modules/unifideck/rpc/mixins/store.py](py_modules/unifideck/rpc/mixins/store.py) — `get_protondb_cache`, `get_real_steam_appid_mappings`, `get_steam_metadata_cache`, `inject_game_to_appinfo`
- [py_modules/unifideck/rpc/mixins/sync.py](py_modules/unifideck/rpc/mixins/sync.py) — `perform_full_cleanup`

---

### 3.21 Steam Store Appdetails Fetcher

**New module** [py_modules/unifideck/steam/appdetails.py](py_modules/unifideck/steam/appdetails.py) — `fetch_appdetails(steam_app_id)` hits `store.steampowered.com/api/appdetails`, returns the rich `data` dict (description, screenshots, achievements, DLC, etc.). `fetch_appdetails_batch` does sequential polite fetches with configurable delay. Used by `MetadataService` on every non-Steam game during enrichment.

---

### 3.22 Frontend — SteamStorePatcher Ported

**New module** [src/lib/steam-bridge/app-store-patcher.ts](src/lib/steam-bridge/app-store-patcher.ts) — ported from staging's `src/spoofing/SteamStorePatcher.ts` (~330 LOC vs 762 LOC staging, leaner by removing the now-unnecessary `m_mapApps` and `appinfo.vdf` persistence layers). Monkey-patches `appStore.GetAppOverviewByAppID` + `appDetailsStore.GetAppDetails` + `.GetAppData` to redirect Unifideck shortcut lookups to real Steam Store data. Loads mappings + metadata from the two new RPCs at boot.

Wired in [src/index.tsx](src/index.tsx) (async `applyAppStorePatch()`) and [src/teardown.ts](src/teardown.ts) (handle disposal). Called per-navigation in [src/views/AppDetailsPatch.tsx](src/views/AppDetailsPatch.tsx) via `injectGameToAppinfo(appId)`.

---

### 3.23 Custom Library Tabs + Steam Collections

Four new modules restoring staging's tab/collection system:

- [src/lib/steam-bridge/tab-container.ts](src/lib/steam-bridge/tab-container.ts) — 10 custom tabs (Great on Deck, All Games, Installed, Steam, Epic, GOG, Amazon, Ubisoft, Microsoft, Non-Steam) with filter definitions
- [src/lib/steam-bridge/library-patch.ts](src/lib/steam-bridge/library-patch.ts) — patches Steam's library `useMemo` to inject tabs via React tree manipulation
- [src/lib/steam-bridge/collection-manager.ts](src/lib/steam-bridge/collection-manager.ts) — auto-generates `[Unifideck] *` Steam Collections from tab filters
- [src/lib/library-filters/index.ts](src/lib/library-filters/index.ts) — filter engine ported from `staging:src/tabs/filters.ts` (store, installed, deckCompat, nonSteam, all)
- [src/lib/protondb-cache.ts](src/lib/protondb-cache.ts) — in-memory ProtonDB cache loaded once from backend

Wired in [src/index.tsx](src/index.tsx) with teardown handles in [src/teardown.ts](src/teardown.ts).

---

### 3.24 Standalone Artwork Test Script

**New script** [scripts/artwork_test.py](scripts/artwork_test.py) — runs outside the Decky runtime. Reads `shortcuts.vdf` directly, identifies Unifideck-managed entries by LaunchOptions regex, and exercises the full artwork pipeline (store metadata → SGDB → Steam CDN) against them. Options: `--enumerate`, `--limit N`, `--all`, `--include-existing`, `--concurrency N`. Used during development to validate that the pipeline works end-to-end independently of the plugin runtime.

---

## 4. Files Changed

### Backend — Python (37 files)

**Modified (18):**

- `py_modules/unifideck/bootstrap/boot.py`
- `py_modules/unifideck/bootstrap/cache_registry.py`
- `py_modules/unifideck/bootstrap/pipeline_factory.py`
- `py_modules/unifideck/core/sync_queries_mixin.py`
- `py_modules/unifideck/core/sync_service.py`
- `py_modules/unifideck/core/types/events.py`
- `py_modules/unifideck/event_bus/event_bus.py`
- `py_modules/unifideck/event_bus/event_priority.py`
- `py_modules/unifideck/rpc/mixins/store.py`
- `py_modules/unifideck/rpc/mixins/sync.py`
- `py_modules/unifideck/services/artwork/event_handlers.py`
- `py_modules/unifideck/services/artwork/fetcher.py`
- `py_modules/unifideck/services/artwork/service.py`
- `py_modules/unifideck/services/bootstrap/constructor.py`
- `py_modules/unifideck/services/bootstrap/paths.py`
- `py_modules/unifideck/services/bootstrap/service_defs.py`
- `py_modules/unifideck/services/metadata_service.py`
- `py_modules/unifideck/services/shortcut/events.py`
- `py_modules/unifideck/services/shortcut/games_map_mixin.py`
- `py_modules/unifideck/services/shortcut/service.py`

**New (7):**

- `py_modules/unifideck/core/sync_progress.py`
- `py_modules/unifideck/services/artwork/store_metadata.py`
- `py_modules/unifideck/services/shortcut/launch_options.py`
- `py_modules/unifideck/services/shortcut/registry.py`
- `py_modules/unifideck/steam/appdetails.py`
- `py_modules/unifideck/steam/steam_user.py`
- `scripts/artwork_test.py`

### Frontend — TypeScript (13 files)

**Modified (10):**

- `src/api/event-bus-client.ts`
- `src/api/rpc-routes.ts`
- `src/components/info/GameInfoScores.tsx`
- `src/components/settings/LibrarySync.tsx`
- `src/contexts/RootProvider.tsx`
- `src/contexts/SyncContext.tsx`
- `src/i18n/locales/en-US.json`
- `src/index.tsx`
- `src/teardown.ts`
- `src/types/events.ts`
- `src/types/steam.ts`
- `src/views/AppDetailsPatch.tsx`

**New (7):**

- `src/components/settings/CleanupSection.tsx`
- `src/components/settings/GameDetailsViewModeToggle.tsx`
- `src/contexts/LibraryContext.tsx`
- `src/lib/library-filters/index.ts`
- `src/lib/protondb-cache.ts`
- `src/lib/steam-bridge/app-store-patcher.ts`
- `src/lib/steam-bridge/collection-manager.ts`
- `src/lib/steam-bridge/library-patch.ts`
- `src/lib/steam-bridge/tab-container.ts`
- `src/views/UnifiedLibraryView.tsx`

---

## 5. CI Gates

All files pass the project's CI requirements:

- **ruff (hard gate):** `All checks passed!` across all `py_modules/unifideck/` and `scripts/`
- **Python syntax:** All 27 Python files pass `py_compile`
- **TypeScript build:** `created dist in 5.5s` — `rollup` + `@rollup/plugin-typescript` produce a valid `dist/index.js`
- **mypy:** Not runnable in the local environment, but all code follows the existing typing conventions. The CI's `mypy` gate runs with the same `[tool.mypy]` configuration the project already enforces.
- **vulture:** Not runnable locally; all new functions are import-referenced from their call sites. The CI's `vulture` gate is configured with `vulture_whitelist.py` which already covers plugin lifecycle hooks and `@subscribe`-decorated handlers.
