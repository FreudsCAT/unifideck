# Sync / Force Sync — Gap Analysis vs. `origin/staging`

> **🗄️ ARCHIVED (2026-06-22) — mostly resolved.** This 2026-05-18 analysis drove later
> fixes; the bulk of the P0/P1 gaps it lists are now implemented in
> `services/sync_service.py`, `core/sync_progress.py`, and `services/artwork/`. Kept as a
> historical record of what was addressed.

> Reference branch: `origin/staging` (commit `30ec3b1`, 0.6.1-era — last known-working production pipeline)
> Current branch: `for-pr-0.7` (new service-based architecture)
> Generated: 2026-05-18

This document maps the **full sync** and **force sync** pipelines on both branches, lists every behavioral gap, and prescribes an implementation plan. File:line references are exact; verify with `git show origin/staging:<path>` for staging citations.

---

## 1. Pipeline at a glance

### 1.1 `origin/staging` (monolithic in `main.py`)

```
RPC (main.py)
  └─ sync_libraries / force_sync_libraries
       └─ _start_sync_task_locked(SyncRequest)         # request queue + merge
            └─ _run_sync_request → _sync_libraries_impl (or _force_*)
                 1. fetch each store sequentially      # epic, gog, amazon, ubisoft, microsoft
                 2. checking_installed phase           # per-store install detection
                 3. generate app_ids                   # crc32(exe|store:id)
                 4. Steam Store presence lookup        # 10 concurrent, cache w/ -1 sentinel
                 5. UnifiDB lookup                     # 5 concurrent, cache w/ None sentinel
                 6. SteamGridDB lookup                 # 30 concurrent, cache w/ -1 sentinel
                 7. artwork download                   # batch + 2-pass retry
                 8. write shortcuts.vdf                # add_games_batch / force_update_games_batch
                    + VDF de-duplication by metadata score
                 9. background: compat_fetcher.queue_games(...)   # ProtonDB / Deck Verified
                10. background: size_fetcher.queue_games(...)     # install sizes
                11. event_recorder.record(LIBRARY_SYNC_COMPLETED)
```

Key properties:

- **Single-flight via `_sync_lock`** + **request queue via `_sync_request_lock`** (`main.py:2296-2297`)
- **Request merging**: if a second request arrives before the first finishes, they merge (force wins; flags ORed) — `_merge_sync_requests()` at `main.py:2763`
- **Cache snapshot/restore on cancel**: `_capture_sync_cache_snapshot()` at `main.py:2671`, `_restore_sync_cache_snapshot()` at `main.py:~2700` — partial sync cannot leave bad cache state
- **Cancel checkpoints** between phases (`main.py:3044, 3471, 3852`)
- **`fetch_artwork=False`** parameter on `sync_libraries` — skip the slow artwork phase entirely
- **`resync_artwork=True`** on `force_sync_libraries` — clears artwork cache and re-downloads
- **Activity events** recorded to playtime DB at start + end (`main.py:2797-2842`)
- **Post-sync ProtonDB fetch** wired via `self.compat_fetcher.queue_games(...)` (`main.py:3082, 3729`)
- **Install size fetch** wired via `self.size_fetcher.queue_games(...)` (`main.py:3177`)

### 1.2 `for-pr-0.7` (service-based)

```
RPC (rpc/mixins/sync.py)
  └─ sync_libraries → sync_service.sync_all(**kw)
  └─ force_sync_libraries → sync_service.sync_all(force=True)    # resync_artwork is a NO-OP
       └─ SyncService._run_sync (core/sync_service.py:147)
            └─ _setup_sync → emit SYNC_STARTED
            └─ sequential per-store loop (sync_queries_mixin)
                 - emit SYNC_PROGRESS per store
                 - on error: emit SYNC_FAILED + LAUNCHER_STAGE toast
            └─ _finalize_sync (sync_service.py:221)
                 - _apply_dedup_and_emit                # currently a no-op (disabled)
                 - _populate_app_ids
                 - emit SYNC_COMPLETE
            ↓
        Event listeners (subscribed at bootstrap, fan-out async):
          - ShortcutService.reconcile         → emit SHORTCUT_RECONCILE_COMPLETE
          - ArtworkService.batch fetch        → emit POST_SYNC_PHASE_CHANGED(artwork)
          - MetadataService.enrich            → emit POST_SYNC_PHASE_CHANGED(metadata)
```

Key properties:

- **Single-flight via `_lock`** in `SyncService` (`sync_service.py:100`) — `force=True` is **test-only bypass**, not the user-facing force sync
- **No request queue / no merging** — a second call while syncing returns `error="sync_already_running"`
- **No cache snapshot/restore on cancel** — verified by grep (zero matches)
- **Cancel checkpoint only between stores** — no checkpoints inside artwork/metadata phases
- **No `fetch_artwork` parameter** — sync always fetches artwork
- **`resync_artwork` parameter exists at the RPC layer but is a logged no-op** — `rpc/mixins/sync.py:48` explicitly says `TODO: forward to artwork invalidator once the service grows a resync_artwork parameter`
- **No activity event recording** — grep for `LIBRARY_SYNC_STARTED` / playtime event-recorder returns empty
- **`compatibility` module is present but NOT wired to sync** — grep for `BackgroundCompatFetcher` returns zero hits in `services/` and `main.py`
- **No install-size fetcher** — grep returns only legendary internal references
- **Three-source artwork pipeline matches staging** (per-store API → SGDB → Steam Store CDN, `services/artwork/service.py:133`)
- **Reconciliation with reclaim** is preserved (`services/shortcut/reconcile_phases.py:178`)
- **Three-source metadata enrichment** matches (`services/metadata_service.py:74-149`)

---

## 2. Gap inventory

Severity legend: **🔴 P0** = data integrity / user-visible feature broken · **🟠 P1** = silently degraded behavior · **🟡 P2** = polish / consistency · **🟢 P3** = nice-to-have

### 🔴 P0 — Must fix before merging

| #      | Gap                                      | Staging behavior                                                                                                                                                                                                                                                  | Current behavior                                                                                                                      | Impact                                                                                                                                                                     |
| ------ | ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **G1** | `resync_artwork` is a no-op              | `_force_sync_libraries_impl` clears `artwork_attempts_cache.json` when `resync_artwork=True` ([`main.py:3659`](/tmp/unifideck-staging/main.py#L3659))                                                                                                             | `rpc/mixins/sync.py:43-53` logs the param then ignores it. Force-sync modal's "re-download artwork" checkbox does nothing.            | User toggles "re-download artwork" → cache is reused → broken/stale art persists. **Frontend lies to user.**                                                               |
| **G2** | No cancel-safe cache snapshot            | `_capture_sync_cache_snapshot()` saves cache state at sync start; `_restore_sync_cache_snapshot()` reverts on cancel ([`main.py:2671-2707`](/tmp/unifideck-staging/main.py#L2671))                                                                                | No snapshot. If user cancels mid-artwork, half-written cache (e.g. some real_appid mappings, partial metadata) persists.              | Cancel-then-resync leaves the user with inconsistent metadata state. Subsequent syncs may skip lookups that previously failed mid-way.                                     |
| **G3** | No `fetch_artwork=False` skip-path       | `sync_libraries(fetch_artwork=False)` skips the artwork phase entirely ([`main.py:2944`](/tmp/unifideck-staging/main.py#L2944))                                                                                                                                   | Param doesn't exist. Sync always blocks on artwork.                                                                                   | Background / auto-syncs that don't need artwork still pay the full latency.                                                                                                |
| **G4** | No request queueing / auth-chained syncs | `request_auth_sync(source='auth:epic')` queues a sync to run after current; `_merge_sync_requests()` merges queued requests; response carries `restart_pending=True` so frontend auto-retries ([`main.py:2898-2937, 2763`](/tmp/unifideck-staging/main.py#L2898)) | Concurrent call → `error="sync_already_running"`. No `request_auth_sync`, no `_pending_auth_sync_request`, no `restart_pending` flag. | After login to a new store, no automatic sync. User must manually press Sync. Worse: if a sync is running when login completes, the post-auth refresh is silently dropped. |

### 🟠 P1 — Silently degraded functionality

| #       | Gap                                         | Staging behavior                                                                                                                                                                                                                                                                                           | Current behavior                                                                                                                  | Impact                                                                                                                                     |
| ------- | ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **G5**  | ProtonDB / Deck Verified compat not fetched | `self.compat_fetcher.queue_games(all_games); self.compat_fetcher.start()` after each sync ([`main.py:3082, 3729`](/tmp/unifideck-staging/main.py#L3082)). Backed by `BackgroundCompatFetcher` (still present in current at `py_modules/unifideck/compatibility/`)                                          | `compatibility` module exists but **no caller** — grep for `BackgroundCompatFetcher` in `services/` and `main.py` returns nothing | Compat badges on game tiles will be empty unless populated some other way. The whole ProtonDB integration is dark.                         |
| **G6**  | No install-size fetcher                     | `self.size_fetcher.queue_games(all_games)` after sync ([`main.py:3177`](/tmp/unifideck-staging/main.py#L3177)) — populates per-game install size for UI                                                                                                                                                    | Not present in current `unifideck/` modules                                                                                       | Game tiles won't show install size. Storage UI loses data.                                                                                 |
| **G7**  | No activity event recording for sync        | `LIBRARY_SYNC_STARTED` + `LIBRARY_SYNC_COMPLETED` events written to playtime DB w/ duration, game count, artwork count ([`main.py:2797-2842`](/tmp/unifideck-staging/main.py#L2797))                                                                                                                       | Playtime DB exists but no sync events recorded                                                                                    | Activity panel / analytics for "last library sync" disappears.                                                                             |
| **G8**  | No negative cache (`-1` / `None` sentinels) | Staging stores `-1` for "Steam doesn't have this title" and `None` for "UnifiDB doesn't have it" — skipped on subsequent syncs ([`main.py:3261, 3343`](/tmp/unifideck-staging/main.py#L3261))                                                                                                              | Verified: no `-1` sentinel logic in `services/artwork/service.py` or `services/metadata_service.py`. Cache has TTL only.          | Every sync re-queries Steam Store + UnifiDB + SGDB for games they don't know about → wasted bandwidth, slower sync, risk of rate-limiting. |
| **G9**  | No VDF post-write dedup                     | `_deduplicate_shortcuts_data()` scores duplicate shortcuts by metadata richness (LastPlayTime > icon > playtime > tags > exe quality) and keeps the best ([`shortcuts_manager.py:1510-1611`](/tmp/unifideck-staging/py_modules/unifideck/shortcuts/shortcuts_manager.py#L1510)). Called before each write. | No equivalent in `services/shortcut/`. If Steam ever inserts a duplicate (it does occasionally), we don't clean it up.            | Duplicate non-Steam entries accrete over time; orphan icons / playtime / tags get lost on the "wrong" copy.                                |
| **G10** | Cancel checkpoints only between stores      | Staging checks cancel flag inside artwork download loop ([`main.py:3471`](/tmp/unifideck-staging/main.py#L3471)) and after shortcut write ([`main.py:3852`](/tmp/unifideck-staging/main.py#L3852))                                                                                                         | Only `_cancel_event.is_set()` check between stores; artwork/metadata services have their own queues but no central cancel signal  | Cancel during artwork phase → artwork phase keeps running. User has to wait.                                                               |

### 🟡 P2 — Polish / consistency

| #       | Gap                                                | Staging                                                                                                                                                                                                                              | Current                                                                                                                                                    | Impact                                                                                                                   |
| ------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **G11** | Cooldown changed 5s → 30s                          | 5s after sync ([`index.tsx:785`](/tmp/unifideck-staging/src/index.tsx#L785))                                                                                                                                                         | 30s via `useSyncCooldown` ([`src/hooks/useSyncCooldown.ts:18`](src/hooks/useSyncCooldown.ts#L18))                                                          | User-visible: 6× wait. Intentional per commit msg but worth a config knob.                                               |
| **G12** | Missing `proton_setup` progress phase              | Staging exposes a `proton_setup: (95, 98)` phase ([`main.py:1538`](/tmp/unifideck-staging/main.py#L1538)) for the final compat-fetch tick                                                                                            | Current `PHASE_RANGES` ([`core/sync_progress.py:22-35`](py_modules/unifideck/core/sync_progress.py#L22)) jumps from `metadata (90-98)` to `complete (100)` | Once compat is wired (G5), the progress bar will stall at 98% with no indication of what's happening.                    |
| **G13** | No `migrate_managed_shortcut_appids`               | Staging migrates legacy appids that didn't include `store:id` in the hash, preventing collisions ([`shortcuts_manager.py:1248-1340`](/tmp/unifideck-staging/py_modules/unifideck/shortcuts/shortcuts_manager.py#L1248))              | Not ported                                                                                                                                                 | Users coming from 0.6.x will keep their old (collision-prone) appids; some games may be reclaimed under the wrong title. |
| **G14** | No `is_protected_shortcut_id()` concept            | Staging marks auth-forwarder shortcuts (e.g. `epic:epic-auth`) as protected from removal in `add_games_batch` ([`shortcuts_manager.py:78-80, 1269`](/tmp/unifideck-staging/py_modules/unifideck/shortcuts/shortcuts_manager.py#L78)) | Reconcile preserves auth shortcuts but by ad-hoc heuristics. No central protected-id list.                                                                 | Risk that a future refactor removes auth shortcuts during reconcile. Brittle.                                            |
| **G15** | No `_backup_cache_file` before force sync          | Staging backs up steam_real_appid + steam_metadata + unifidb + metacritic caches before clearing them on force sync ([`main.py:3624-3659`](/tmp/unifideck-staging/main.py#L3624))                                                    | No backup. Force sync clearing is destructive.                                                                                                             | If force sync fails halfway, all metadata is gone; next sync starts cold.                                                |
| **G16** | No `IMPORTANT: Steam restart required!` log banner | Staging logs a 3-line warning when shortcuts added/changed ([`main.py:3574-3578`](/tmp/unifideck-staging/main.py#L3574))                                                                                                             | Frontend modal handles user prompt — no equivalent log banner                                                                                              | Diagnostic noise: when reading logs to debug "why don't my games show up", the explicit "restart Steam" hint is gone.    |

### 🟢 P3 — Nice-to-have

| #       | Gap                                   | Notes                                                                                                                                                                                                                                               |
| ------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **G17** | Per-store refresh button in UI        | Both branches define `unifideck://refresh-library/<store>` but neither has a UI button. The backend handler exists in current (`actions/dispatch.py:87`); only missing the UI surface.                                                              |
| **G18** | Microsoft subscription pre-sync check | Current has `MicrosoftSubscriptionService` ([`services/microsoft_subscription/`](py_modules/unifideck/services/microsoft_subscription/)) that can short-circuit the MS store sync. Staging didn't have this — improvement, not gap.                 |
| **G19** | Account-switch detection pre-sync     | Both branches handle account switches but staging checks before reconciliation ([`main.py:2133`](/tmp/unifideck-staging/main.py#L2133)). Current branch's `AccountService` emits `ACCOUNT_SWITCHED`; need to verify the pre-sync check still fires. |

---

## 3. What's better in `for-pr-0.7` (don't regress these)

- **Event-driven architecture** — `SYNC_STARTED`, `SYNC_PROGRESS`, `SYNC_COMPLETE`, `SYNC_FAILED`, `SYNC_CANCELLED`, `SHORTCUT_RECONCILE_COMPLETE`, `POST_SYNC_PHASE_CHANGED`. Staging was polling-only.
- **Frontend `SyncContext`** — single source of truth, replaces ~10 props threaded through index.tsx.
- **Cooldown survives QAM dismount** via module-level `cooldownEndsAt` ([`src/hooks/useSyncCooldown.ts:22`](src/hooks/useSyncCooldown.ts#L22)).
- **Cancel button shows "Cancelling…"** during the RPC ([`src/components/settings/LibrarySync.tsx:90-92`](src/components/settings/LibrarySync.tsx#L90)).
- **Toasts decoupled** via `LAUNCHER_STAGE` event channel.
- **Per-store error toasts include a deep-link** to retry (`unifideck://refresh-library/<store>`) — staging didn't have this.
- **Background post-sync phases** (artwork, metadata) emit `POST_SYNC_PHASE_CHANGED(active=False)` independently, so the progress bar correctly waits for both before declaring complete.
- **Shortcut reclaim by AppID** works the same and is cleaner code ([`services/shortcut/reconcile_phases.py:178`](py_modules/unifideck/services/shortcut/reconcile_phases.py#L178)).

---

## 4. Implementation plan

Order is dependency-driven: snapshot/cancel first (so partial restores work for later phases), then plumb the missing data flow, then the post-sync hooks.

### Phase A — Force-sync correctness (P0)

**A1. Wire `resync_artwork` end-to-end** _(closes G1)_

- Add `resync_artwork: bool = False` to `SyncService.sync_all(force=False, resync_artwork=False)` ([`py_modules/unifideck/core/sync_service.py:116`](py_modules/unifideck/core/sync_service.py#L116)).
- Pass it through `_run_sync` → store on `self._resync_artwork` (transient, reset in `_setup_sync`).
- In `_finalize_sync`, before emitting `SYNC_COMPLETE`, if `_resync_artwork=True`: clear the `sgdb_fetch` cache namespace + delete `artwork_attempts_cache.json` (need to add this cache namespace if absent — check `core/cache_manager.py`).
- Remove the TODO comment at [`rpc/mixins/sync.py:43-53`](py_modules/unifideck/rpc/mixins/sync.py#L43) and forward the param: `return await self.sync_service.sync_all(force=True, resync_artwork=resync_artwork, **kw)`.
- Add a test: call `force_sync_libraries(resync_artwork=True)`, assert the artwork cache is empty after.

**A2. Add `fetch_artwork=False` skip path** _(closes G3)_

- Add `fetch_artwork: bool = True` to `sync_all`.
- Threaded into `_finalize_sync`: if `False`, don't start the artwork phase (set `progress.skip_artwork()` or emit `POST_SYNC_PHASE_CHANGED(artwork, active=False, total=0)` immediately).
- Propagate via RPC: `sync_libraries(fetch_artwork: bool = True, ...)`.
- ArtworkService's `_on_sync_complete` needs to check a flag on the event payload (`fetch_artwork`) and bail if False.

**A3. Cache snapshot/restore on cancel** _(closes G2)_

- Add `CacheManager.snapshot() -> dict[str, dict]` / `CacheManager.restore(snapshot)` methods at [`py_modules/unifideck/core/cache_manager.py`](py_modules/unifideck/core/cache_manager.py).
  - Snapshot: deep-copy `_stores` keyed by namespace.
  - Restore: replace each `CacheStore`'s in-memory dict + atomic persist.
- In `_setup_sync` ([`sync_service.py:147`](py_modules/unifideck/core/sync_service.py#L147)): `self._cache_snapshot = self._cache.snapshot()`.
- In cancel-path handler in `_run_sync`: `self._cache.restore(self._cache_snapshot)`.
- On successful complete: discard the snapshot.

### Phase B — Request queueing + auth chaining (P0)

**B1. Add `SyncRequest` queue model** _(closes G4)_

- New dataclass `SyncRequest` in `core/types/domain.py`:
  ```python
  @dataclass
  class SyncRequest:
      kind: Literal["sync", "force"] = "sync"
      source: str = "manual"          # "manual" | "auth:<store>" | "background" | "scheduled"
      fetch_artwork: bool = True
      resync_artwork: bool = False
  ```
- In `SyncService`:
  - Add `self._pending_request: SyncRequest | None = None` + `self._request_lock = asyncio.Lock()`.
  - `sync_all(...)` becomes a thin wrapper that builds a `SyncRequest` and calls `_enqueue(request)`.
  - `_enqueue(request)`: under `_request_lock`, if `_lock.locked()`: merge into `_pending_request` (force wins, OR the flags) and return `{"queued": True, "restart_pending": True}`. Else acquire `_lock` and start `_run_sync(request)`.
  - After `_run_sync` finishes, check `_pending_request` and start it.
- New public method `request_auth_sync(source: str)` that mirrors staging's behavior — called by AuthDispatcher after successful store login.
- Bubble `restart_pending` to the frontend response so `SyncContext` knows to auto-listen for the next `SYNC_STARTED`.

### Phase C — Post-sync data hooks (P1)

**C1. Wire `BackgroundCompatFetcher`** _(closes G5)_

- Create new service `services/compatibility/service.py` (mirror artwork/metadata pattern):
  - `__init__` takes `cache`, `bus`, the existing `BackgroundCompatFetcher` from `unifideck/compatibility/`.
  - Subscribe to `SYNC_COMPLETE`: call `fetcher.queue_games(games); fetcher.start()`.
  - Emit `POST_SYNC_PHASE_CHANGED(phase="proton_setup", active=False)` when done.
- Register in `services/bootstrap/service_defs.py` alongside artwork/metadata.
- Add `proton_setup: (95, 98)` to `PHASE_RANGES` in `core/sync_progress.py` (closes G12 in the same change).

**C2. Add `SizeFetcher` service** _(closes G6)_

- Port `size_fetcher` logic from staging (was a separate module). Pattern: read game install dirs, compute total size, cache per-game in a new `game_size` cache namespace.
- Subscribe to `SYNC_COMPLETE` like compat fetcher.
- Expose via `get_game_info` so the UI reads sizes alongside other metadata.

**C3. Sync activity events** _(closes G7)_

- In `SyncService._setup_sync`: `self._playtime_recorder.record_event(SYNC_STARTED, store_count=...)`.
- In `_finalize_sync`: record `SYNC_COMPLETED` with duration_ms, game count, artwork count.
- In cancel path: `SYNC_CANCELLED`.
- Wire `PlaytimeService` (or equivalent) into `SyncService` via constructor.

### Phase D — Cache hygiene (P1)

**D1. Add negative-cache sentinels** _(closes G8)_

- In `services/metadata_service.py`: when Steam Store API returns "not found", cache `real_appid = -1` (or use a `MetadataResult.NOT_FOUND` enum). On `get`, skip lookup if value is the sentinel.
- Same for UnifiDB lookup (`None` payload means "we tried, not found").
- Same for SGDB in `services/artwork/`.
- TTL: negative entries get a longer TTL (7-14 days) to avoid hammering on every sync.
- Frontend impact: zero (these are backend caches only).

**D2. VDF post-write dedup with metadata scoring** _(closes G9)_

- Port `_deduplicate_shortcuts_data` from staging ([`shortcuts_manager.py:1510-1584`](/tmp/unifideck-staging/py_modules/unifideck/shortcuts/shortcuts_manager.py#L1510)) into `services/shortcut/dedup.py`.
- Call it inside `ShortcutService.reconcile` AFTER the reclaim phase, BEFORE persisting.
- Score function: `lastplaytime(2) + icon(1) + playtime_forever(1) + tag_richness(1) + exe_quality(2) + startdir(1) + appname(1) + exe_basename_relevance(1)`.

**D3. Pre-force-sync cache backup** _(closes G15)_

- In `_setup_sync` when `request.kind == "force"`: copy each cache file to `<file>.bak` (drop `.bak` files older than 7 days).
- Restore from `.bak` if force sync fails (catch in `_run_sync`).

### Phase E — Cancellation depth + polish (P1/P2)

**E1. Cancel checkpoints inside post-sync phases** _(closes G10)_

- ArtworkService's batch loop: check `sync_service.is_cancelled()` (new public method that wraps `_cancel_event.is_set()`) between each game.
- MetadataService: same.
- Stop scheduling new fetches after cancel; let in-flight ones drain.

**E2. Legacy AppID migration** _(closes G13)_

- Port `migrate_managed_shortcut_appids` from staging.
- Run once at first sync after upgrade (gated by a `migrations.appid_v2` config key).

**E3. Protected shortcut registry** _(closes G14)_

- Add `services/shortcut/protected.py` exposing `PROTECTED_IDS = frozenset({"epic:epic-auth", "ubisoft:upc-auth", "amazon:amazon-auth", ...})`.
- `reconcile` consults this set before deleting any shortcut.

**E4. Sync log banners** _(closes G16)_

- In `_finalize_sync` after `SHORTCUT_RECONCILE_COMPLETE`, if `added > 0 or removed > 0`: emit the 3-line `IMPORTANT: Steam restart required!` log block via the same logger.

### Phase F — UX (P2/P3)

**F1. Configurable cooldown** _(addresses G11)_

- Read `sync.cooldown_seconds` from config; default 30 but let users dial it down.
- Frontend: hook reads from `useConfig`, replaces `COOLDOWN_MS` constant.

**F2. Per-store refresh button** _(closes G17)_

- Render a small refresh icon next to each store row in `StoresList.tsx`.
- onClick → `dispatch(unifideck://refresh-library/<store>)` (handler already exists at [`actions/dispatch.py:87`](py_modules/unifideck/actions/dispatch.py#L87)).

---

## 5. Suggested PR slicing

To keep diffs reviewable:

| PR  | Scope                                                              | Closes          |
| --- | ------------------------------------------------------------------ | --------------- |
| 1   | Force-sync param wiring (resync_artwork + fetch_artwork)           | G1, G3          |
| 2   | Cache snapshot/restore + cancel-safety                             | G2, G10         |
| 3   | SyncRequest queue + auth-chained sync                              | G4              |
| 4   | Compatibility + size + activity post-sync services                 | G5, G6, G7, G12 |
| 5   | Cache hygiene (negative cache + VDF dedup + force-sync backup)     | G8, G9, G15     |
| 6   | Shortcut hardening (legacy migration + protected set + log banner) | G13, G14, G16   |
| 7   | UX (configurable cooldown + per-store refresh button)              | G11, G17        |

Each PR ships behind no feature flag because each closes a specific regression — staging users today already had these behaviors.

---

## 6. Verification plan

Per PR, manual + log checks:

1. **`tail -f ~/homebrew/logs/Unifideck/*.log`** while running a sync. Expected lines:
   - `[SyncService] sync complete — N games across M stores in Xms`
   - `[shortcut] reconcile added=N removed=N kept=N reclaimed=N`
   - `[artwork] phase complete (done=N total=N)`
   - `[metadata] phase complete (done=N total=N)`
   - After PR 4: `[compatibility] phase complete (done=N total=N)`
2. **Force sync with "re-download artwork" checked** (PR 1) → grep log for `[sync] resync_artwork=True` → verify `~/.local/share/unifideck/metadata/artwork_attempts_cache.json` is gone after, then re-created.
3. **Cancel mid-sync, then re-sync** (PR 2) → verify cache files unchanged from pre-sync state.
4. **Log in to Epic while a sync is running** (PR 3) → response carries `"restart_pending": true` → verify a second sync auto-starts after the first finishes.
5. **Cleanup + sync, then check ProtonDB badges** (PR 4) → game tiles show tier (Platinum/Gold/Silver/Bronze).
6. **Sync twice without changes** (PR 5) → second sync should not re-query Steam Store / SGDB / UnifiDB for games that returned "not found" the first time. Verify by log line count.
7. **Per-store refresh button** (PR 7) → click → log shows `dispatch refresh-library epic` → only Epic library re-fetched.

---

## 7. Out of scope

- CDP-injection auth flow (separate from sync)
- Cloud-save sync (own RPC surface, `services/cloud_save/`)
- Launch flow (`services/launcher/`)
- Microsoft xCloud (specialty case, owns its own progress)

These are well-isolated from the library-sync pipeline and not part of any identified gap.

---

## 8. Frontend progress + SGDB deep-dive

Added 2026-05-18 after a second pass focused on progress counters / i18n / SGDB. Every claim here was verified by reading `sync_progress.py` and grepping the locale — not agent output.

### 8.1 Progress payload is broken at the source

The backend's `SyncProgress.to_dict()` ([py_modules/unifideck/core/sync_progress.py:177-195](py_modules/unifideck/core/sync_progress.py#L177-L195)) ships a payload that lies to the frontend:

```python
return {
    ...
    "steam_total": self.steam_total,
    "steam_synced": self.steam_synced,
    "unifidb_total": 0,                          # ← hardcoded zero
    "unifidb_synced": 0,                         # ← hardcoded zero
    "metacritic_total": self.metadata_total,    # ← renamed under wrong key
    "metacritic_synced": self.metadata_synced,  # ← renamed under wrong key
}
```

This means:

- The frontend can never display UnifiDB progress, even if the backend tracked it.
- The frontend's `progress.metacritic_total` is actually whatever the _generic metadata_ phase counter is — not Metacritic at all. Anywhere the locale says "Fetching Metacritic metadata…", the user is reading a lie.
- Staging shipped four distinct counter pairs (`steam`, `unifidb`, `metacritic`, `rawg`) — [staging:main.py:1681-1703](https://github.com/mubaraknumann/unifideck/blob/staging/main.py#L1681). Current ships two real ones and two fakes.

### 8.2 No `increment_unifidb()`, no `increment_metacritic()`, no `increment_rawg()`

The `SyncProgress` class only defines:

- `increment_artwork(title)` → label `sync.downloadingArtwork`
- `increment_steam(title)` → label `sync.extractingSteamMetadata`
- `increment_metadata(title)` → label `sync.extractingMetadata`

There is no UnifiDB increment, no Metacritic increment, no RAWG increment. Whatever the metadata pipeline is doing internally, it cannot tick a phase-specific counter even if it wanted to. Staging had four increments, one per source, each emitting its own i18n label.

### 8.3 Half of `PHASE_RANGES` is dead code

[sync_progress.py:22-35](py_modules/unifideck/core/sync_progress.py#L22-L35) defines 12 phase ranges. Only **7** are ever set as `status`:

| Status               | Defined range | Ever set?  | Where set                  |
| -------------------- | ------------- | ---------- | -------------------------- |
| `idle`               | (0, 0)        | ✓          | initial                    |
| `fetching`           | (0, 10)       | ✓          | `start_fetching` line 60   |
| `checking_installed` | (10, 20)      | ✗ **DEAD** | nowhere                    |
| `syncing`            | (20, 40)      | ✓          | `start_store_sync` line 68 |
| `steam_metadata`     | (40, 50)      | ✗ **DEAD** | nowhere                    |
| `unifidb_lookup`     | (50, 55)      | ✗ **DEAD** | nowhere                    |
| `sgdb_lookup`        | (55, 60)      | ✗ **DEAD** | nowhere                    |
| `artwork`            | (60, 90)      | ✓          | `start_artwork` line 81    |
| `metadata`           | (90, 98)      | ✓          | `start_metadata` line 92   |
| `complete`           | (100, 100)    | ✓          | `mark_complete` line 101   |
| `error`              | (100, 100)    | ✓          | `mark_error` line 106      |
| `cancelled`          | (100, 100)    | ✓          | `mark_cancelled` line 111  |

The progress bar will jump straight from `syncing` (40%) → `artwork` (60%) with a 20-point gap that _should_ be `steam_metadata → unifidb_lookup → sgdb_lookup` but is invisible to the user. From the UX side: every sync looks like the bar freezes at 40% for several seconds, then resumes at 60% — there's no label telling them why.

### 8.4 i18n key drift

Backend emits these `current_game.label` keys during sync:

| Emitted by                         | Key                            | Exists in en-US.json? |
| ---------------------------------- | ------------------------------ | --------------------- |
| `start_fetching` (line 63)         | `sync.fetchingGameLists`       | ✓                     |
| `start_store_sync` (line 70)       | `sync.fetchingStore`           | ✗ **MISSING**         |
| `start_artwork` default (line 79)  | `artwork.checking`             | needs verification    |
| `start_metadata` default (line 90) | `sync.extractingMetadata`      | ✓                     |
| `mark_complete` (line 102)         | `sync.completed`               | ✓                     |
| `increment_artwork` (line 120)     | `sync.downloadingArtwork`      | ✓                     |
| `increment_steam` (line 148)       | `sync.extractingSteamMetadata` | ✓                     |
| `increment_metadata` (line 134)    | `sync.extractingMetadata`      | ✓                     |

The store-sync label is the most visible one — it fires once per store, so for every sync the user sees the raw key string `"sync.fetchingStore"` instead of a translated label.

### 8.5 Frontend doesn't display half of what it could

[src/components/settings/LibrarySync.tsx:96-158](src/components/settings/LibrarySync.tsx#L96-L158) only reads:

- `artwork_total/synced` (when `status === "artwork"`)
- `metadata_total/synced` — but it actually reads from the **wrong fields** (`metacritic_*`?) — needs verification
- `steam_total/synced` (when neither artwork nor metadata)
- `total_games / synced_games`

Staging's LibrarySync.tsx displayed **three** counter rows when their totals were >0 (in addition to games and artwork):

1. `steamMetadataDownloaded`
2. `unifidbMetadataDownloaded` ← gone in current
3. `metacriticMetadataDownloaded` ← gone in current

The current branch displays only Steam (and even that conditionally). UnifiDB / Metacritic progress is invisible. (RAWG was a staging-only source — not in scope; we don't need it anymore.)

### 8.6 SGDB: from 1316 lines to 197

`py_modules/unifideck/steam/steamgriddb.py`:

- Staging: 1316 lines — class-based client with 6-pass title matching, edition stripping, dimension/style filters, multi-source orchestration.
- Current: 197 lines — module-level functions, single-pass autocomplete, no filters, no fallback retries.

The refactor moved orchestration up to `services/artwork/service.py`, but in doing so dropped the search intelligence. The service layer now calls `find_artwork_url(title, kind, ...)` **five times sequentially** per game (one per kind: grid/grid_l/hero/logo/icon), each call doing a fresh title-only search.

#### Lost SGDB capabilities (each is a separate regression)

| Capability                                                                                                                          | Staging location                                                          | Current state                                        |
| ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------- |
| **Title normalization** (®™©, diacritics, smart quotes, `&`/`and`)                                                                  | `steamgriddb.py:_normalize_for_sgdb_match`                                | Title sent verbatim                                  |
| **Edition suffix stripping** (58 known suffixes: "GOTY Edition", "Xbox One Edition", "Remastered", etc.)                            | `steamgriddb.py:_strip_edition_suffix`                                    | Not present                                          |
| **Jaccard match scoring** (0.85 threshold for franchise-confusion guard)                                                            | `steamgriddb.py:_score_sgdb_match`                                        | Not present                                          |
| **6-pass search ladder** (exact → edition-stripped → scored → retry-base → publisher-prefix-strip → fuzzy@0.50)                     | `steamgriddb.py:search_game` lines 234-398                                | Single pass: first autocomplete result               |
| **Publisher prefix strip** ('ea sports', 'tom clancys', 'sid meiers', 'disney pixar', 'dreamworks', 'microsoft')                    | `steamgriddb.py:343-361`                                                  | Not present                                          |
| **Dimension query param for grids** (`dimensions=600x900` portrait, `dimensions=920x430,460x215` landscape)                         | `_fetch_grids_by_dimensions` line 452                                     | No query params sent → unfiltered 50-result page     |
| **Dimension query param for heroes** (`dimensions=1920x620,3840x1240`)                                                              | `_fetch_heroes` line 475                                                  | No query params sent                                 |
| **Style filter** (`styles=alternate,white_logo,no_logo,blurred,material` for grids; `styles=official,white,black,custom` for logos) | `_fetch_grids_by_dimensions`, `_fetch_logos`                              | No query params sent                                 |
| **NSFW / humor filter** (`nsfw=false&humor=false` query param)                                                                      | every fetch method                                                        | Not sent                                             |
| **Relaxed-dimensions fallback** (if narrow fetch empty, retry with `600x900,660x930,342x482` and no style filter)                   | `steamgriddb.py:628-660`                                                  | No retry                                             |
| **5-level asset ranking** (lock → style priority → score → resolution → API position)                                               | `select_best_artwork` line 414-450                                        | 2-level: style + resolution only                     |
| **Per-rank artwork selection** (request "second-best grid" if first is reserved for a sibling)                                      | `select_best_artwork(rank=N)`                                             | Single best only                                     |
| **Batched SGDB fetch** (single API call returns all kinds for one game)                                                             | `get_grid_images` lines 596-617 — 2-3 parallel HTTP calls for all 5 kinds | 5 separate `find_artwork_url` calls per game, serial |
| **Pre-API query cleaning** (strip ®™©, platform suffixes, edition tags before sending to autocomplete)                              | `steamgriddb.py:246-262`                                                  | Title sent raw                                       |

The cumulative effect: a game like **"Assassin's Creed®III Remastered (Xbox One Edition)"** in staging would resolve to the correct SGDB entry via title normalization + edition stripping + scored match. In current, the query goes to autocomplete verbatim, and the first result wins — often the wrong entry, often empty.

### 8.7 New gap inventory (frontend + SGDB)

🔴 **P0**

| #       | Gap                                                                                                                                   | Impact                                                                                                       |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **G20** | `unifidb_total/synced` hardcoded to `0` in payload ([sync_progress.py:191-192](py_modules/unifideck/core/sync_progress.py#L191-L192)) | UnifiDB phase invisible to frontend. Class doesn't even have these fields.                                   |
| **G21** | `metadata_*` exported as `metacritic_*` ([sync_progress.py:193-194](py_modules/unifideck/core/sync_progress.py#L193-L194))            | Frontend's "Metacritic metadata" counter is actually the generic metadata counter — misleading label.        |
| **G22** | No `increment_unifidb`, no `increment_metacritic`, no `increment_rawg`                                                                | Even if frontend rendered them, backend can't tick the counters.                                             |
| **G23** | SGDB title-search broken (single-pass autocomplete, no normalization, no edition stripping, no scoring)                               | Wrong artwork for any title with ®™©, "Edition" suffixes, or publisher prefixes.                             |
| **G24** | SGDB asset fetches send no `dimensions`/`styles`/`nsfw`/`humor` query params                                                          | Grid covers come back at random sizes; landscape (Galaxy 2.0) grids almost never match. Heroes wrong aspect. |

🟠 **P1**

| #       | Gap                                                                                                                            | Impact                                                                                                                 |
| ------- | ------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| **G25** | Backend emits `sync.fetchingStore` ([sync_progress.py:70](py_modules/unifideck/core/sync_progress.py#L70)) — key not in locale | User sees raw key `"sync.fetchingStore"` instead of "Fetching Epic…". Fires once per store per sync.                   |
| **G26** | 4 dead entries in `PHASE_RANGES`: `checking_installed`, `steam_metadata`, `unifidb_lookup`, `sgdb_lookup`                      | Progress bar jumps 40→60 with no label. UX feels stuck.                                                                |
| **G27** | Frontend doesn't render UnifiDB / Metacritic counter rows                                                                      | Even if G20-22 were fixed, the rendering side is missing. Staging displayed three rows (Steam + UnifiDB + Metacritic). |
| **G28** | SGDB called 5× sequentially per game instead of 1 batched call                                                                 | Slower sync (5× the SGDB API latency per game). Hits free-tier rate limits sooner.                                     |
| **G29** | No in-SGDB fallback (no retry on miss with relaxed filters or stripped title)                                                  | Games that miss on the first lookup never get a second chance, even though staging had three retry strategies.         |
| **G30** | 5-level asset ranking reduced to 2-level                                                                                       | Lower-quality grids selected (loses lock-status preference, upvote score, popularity tiebreaker).                      |

🟡 **P2**

| #       | Gap                           | Impact                                                                                           |
| ------- | ----------------------------- | ------------------------------------------------------------------------------------------------ |
| **G31** | No per-rank artwork selection | Can't pick "second-best grid" if first is already used elsewhere. Rare but staging supported it. |

### 8.8 Updated implementation plan

Inserting two new phases between Phase A and the existing Phase F. Renumber accordingly:

#### Phase G — Fix the progress payload (P0)

**G.1 Drop the `metacritic_*` rename, restore separate UnifiDB and Metacritic counters** _(closes G20, G21, G22, G27)_

- Add fields to `SyncProgress.__init__`: `unifidb_total/synced` and `metacritic_total/synced`. Drop the generic `metadata_total/synced` once both replacements are wired.
- Add increment methods: `increment_unifidb(title)` and `increment_metacritic(title)` — each setting its own `current_game.label` (`sync.lookingUpUnifiDB`, `sync.fetchingMetacriticData`).
- Fix `to_dict()` to export the real fields. Remove the hardcoded zeros and the misleading `metadata_*` → `metacritic_*` rename.
- In `services/metadata_service.py`, route each per-source call through the corresponding increment. Currently `MetadataService` calls `progress.increment_metadata()` once per game; split it into per-source ticks (one tick when UnifiDB lookup finishes, one when Metacritic finishes).
- Frontend: in [LibrarySync.tsx:96-158](src/components/settings/LibrarySync.tsx#L96-L158), restore the three counter rows from staging (Steam + UnifiDB + Metacritic) — show each only when its `*_total > 0`. RAWG row is intentionally dropped; we don't need it.

**G.2 Add the missing status transitions** _(closes G26)_

- After `start_fetching`, before each store call: `progress.start_checking_installed(store_name)` setting `status="checking_installed"` and a label.
- Between syncing and artwork, split into `steam_metadata`/`unifidb_lookup`/`sgdb_lookup` phases as MetadataService and ArtworkService warm up.
- Update `_recalc()` to handle each new phase's sub-counter.

**G.3 Add missing i18n keys** _(closes G25)_

- Add `sync.fetchingStore: "Fetching {{store}}…"` to `en-US.json` and 13 other locales.
- Verify `artwork.checking` exists; add if not.
- Audit: emit every `current_game.label` key the backend uses against the locale; report unknowns.

#### Phase H — Restore SGDB intelligence, multi-file (P0)

Staging's `steamgriddb.py` was 1316 lines — that violates the project's **550 LOC volumetry cap** (see [py_modules/unifideck/core/sync_dedup_mixin.py:5-6](py_modules/unifideck/core/sync_dedup_mixin.py#L5-L6) docstring noting the cap). Port the logic into a package, one concern per file. Target layout:

```
py_modules/unifideck/steam/steamgriddb/
    __init__.py        # re-exports the public API (SteamGridDBClient + free functions)
    match.py           # title normalization, edition stripping, scoring   (~250 LOC)
    search.py          # 6-pass search ladder + publisher prefix table     (~200 LOC)
    assets.py          # _fetch_assets with dimension/style/nsfw params    (~150 LOC)
    ranking.py         # 5-level asset ranking + style priority table      (~120 LOC)
    client.py          # SteamGridDBClient: orchestrates the above         (~200 LOC)
    batch.py           # fetch_all_artwork — parallel kind fetch           (~100 LOC)
```

Each file under 300 LOC, well under the cap. The current single-file `py_modules/unifideck/steam/steamgriddb.py` is replaced by the package's `__init__.py` re-exports so existing imports keep working without churn.

**H.1 Create `steamgriddb/match.py`** _(closes G23)_

- `normalize_for_match(title: str) -> str` — lowercase, strip diacritics, ®™© → space, smart-quote conversion, &→and, collapse whitespace. Port staging's `_normalize_for_sgdb_match`.
- `strip_edition_suffix(title: str) -> str` — port the 58-suffix table from staging's `_strip_edition_suffix` (iterative, one suffix per pass until stable).
- `score_match(query: str, candidate: str) -> float` — Jaccard word-set overlap + prefix-match bonus. Port staging's `_score_sgdb_match`.
- Pure functions, no I/O. Unit-testable.
- **Constants**: `PUBLISHER_PREFIXES = frozenset({"ea sports", "tom clancys", "sid meiers", "disney pixar", "dreamworks", "microsoft"})` lives here.

**H.2 Create `steamgriddb/search.py`** _(closes G23, G29)_

- `async def search_game_id(client, query: str) -> int | None` — runs the 6-pass ladder against the SGDB autocomplete API:
  1. Send normalized query → exact match.
  2. Edition-stripped match.
  3. Scored match ≥ 0.85.
  4. Retry with edition-stripped query.
  5. Retry with each publisher prefix stripped.
  6. Fuzzy fallback ≥ 0.50.
- Caller passes the HTTP client (decoupled for testing).
- Returns SGDB game id, or `None` on confident miss.

**H.3 Create `steamgriddb/assets.py`** _(closes G24)_

- `async def fetch_assets(client, game_id: int, kind: str, *, dimensions: str | None = None, styles: str | None = None) -> list[Asset]` — sends `nsfw=false&humor=false` always, plus the optional filters. Returns raw asset list.
- `KIND_DEFAULTS: dict[str, tuple[str|None, str|None]]` lookup table:
  - `"grid"`: `("600x900", "alternate,white_logo,no_logo,blurred,material")`
  - `"grid_l"`: `("920x430,460x215", "alternate,white_logo,no_logo,blurred,material")`
  - `"hero"`: `("1920x620,3840x1240", "alternate,blurred,material")`
  - `"logo"`: `(None, "official,white,black,custom")`
  - `"icon"`: `(None, None)`
- `RELAXED_DIMENSIONS: dict[str, str]` for the fallback retry — `"grid"` → `"600x900,660x930,342x482"`, `"hero"` → `""` (no filter), etc.
- `async def fetch_with_fallback(client, game_id, kind) -> list[Asset]` — calls `fetch_assets` once with defaults; if empty, retries with relaxed dimensions and no style filter.

**H.4 Create `steamgriddb/ranking.py`** _(closes G30, G31)_

- `STYLE_PRIORITY: dict[str, int]` — `{"alternate":0, "blurred":1, "material":1, "no_logo":1, "white_logo":2}`.
- `def rank_assets(assets: list[Asset]) -> list[Asset]` — return assets sorted by `(not is_locked, style_priority, -score, -resolution, api_position)`. Drop NSFW/humor flagged entries first (defensive — they should already be filtered by the API param).
- `def pick_best(assets: list[Asset], rank: int = 0) -> Asset | None` — return `ranked[rank]` or `None`.

**H.5 Create `steamgriddb/batch.py`** _(closes G28)_

- `async def fetch_all_artwork(client, game_id: int, only_kinds: frozenset[str] | None = None) -> dict[str, Asset | None]` — parallel `asyncio.gather` across the kinds requested, applying `fetch_with_fallback` per kind, then `pick_best` per kind.
- Returns `{"grid": Asset, "hero": None, ...}` — `None` for kinds with no result.
- This is what `services/artwork/service.py` calls — one call per game instead of looping `find_artwork_url` × 5.

**H.6 Refactor `steamgriddb/client.py`** _(thin orchestrator)_

- `class SteamGridDBClient` — owns `aiohttp.ClientSession`, API key, base URL, auth header. Exposes:
  - `async def search_game_id(query)` → delegates to `search.search_game_id(self, query)`.
  - `async def fetch_all_artwork(game_id, only_kinds=None)` → delegates to `batch.fetch_all_artwork(...)`.
  - `async def close()` — closes the session.
- Stays under 200 LOC. No business logic; just HTTP plumbing + module composition.

**H.7 Update `services/artwork/`** _(integration)_

- `services/artwork/fetcher.py` currently calls `find_artwork_url(title, kind, ...)` 5× per game. Replace with:
  ```python
  game_id = await client.search_game_id(title)
  if game_id is None:
      return {kind: None for kind in needed_kinds}
  return await client.fetch_all_artwork(game_id, only_kinds=needed_kinds)
  ```
- This is one search + one batched fetch per game (vs the current 5 sequential round-trips).

**H.8 Wire to current event/cache/log conventions** _(architecture compliance)_

- Logger names: every module logs under `[sgdb.<module>]` (e.g. `[sgdb.search]`, `[sgdb.assets]`) so logs are greppable per concern.
- Negative-cache hits use the existing `sgdb_fetch` namespace from `core/cache_manager.py` — no new namespace needed.
- No new event types; `services/artwork/event_handlers.py` keeps emitting `POST_SYNC_PHASE_CHANGED(phase="artwork", ...)`.

### 8.9 Updated PR slicing

The previous slicing (PRs 1-7) addressed the backend-only gaps. Adding three more:

| PR  | Scope                                                                                                                                    | Closes                       |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| 8   | Progress payload fix (real counters, status transitions, missing i18n)                                                                   | G20, G21, G22, G25, G26, G27 |
| 9   | SGDB intelligence — multi-file package (title normalization + 6-pass search + dimension/style filters + batched fetch + 5-level ranking) | G23, G24, G28, G29, G30, G31 |
| 10  | Post-sync completion fix (try/finally guards, watchdog, `start_metadata` ownership)                                                      | G32                          |

PR 10 should ship **first** — it's a P0 stuck-state bug affecting every sync that hits an edge case. PR 8 ships **before** PR 4 because PR 4 adds new phases that need real counters and labels. PR 9 is independent and can ship in parallel.

### 8.10 Additional verification

After PR 8: `tail -f` the log during a sync — expect to see `[progress] phase=checking_installed`, `[progress] phase=steam_metadata`, `[progress] phase=unifidb_lookup`, etc. Frontend should show three distinct counter rows during the metadata phase (Steam + UnifiDB + Metacritic).

After PR 9: with `[sgdb.search]` DEBUG logging on, for a game like "Watch Dogs® 2 - Deluxe Edition", expect log lines `[sgdb.search] query="watch dogs 2 deluxe edition"` → `[sgdb.search] exact match` or `[sgdb.search] edition-stripped match: 'watch dogs 2'`. Manually verify cover art for ~5 games against SteamGridDB website to confirm correct entry selected.

---

## 9. Sync hangs at the end (post-sync stuck state)

The user reports: "the sync doesn't close immediately after the final step." This is a separate, reproducible bug — root cause traced.

### 9.1 The chain that _should_ fire

After per-store fetch completes, [`_finalize_sync`](py_modules/unifideck/core/sync_service.py#L221) does:

1. `self._post_sync_pending = {"artwork", "metadata"}` (line 256)
2. emits `SYNC_COMPLETE`
3. releases `_lock`

Then two services fan out concurrently:

- `ArtworkService._on_sync_complete` → spawns batch task → done-callback emits `POST_SYNC_PHASE_CHANGED(phase="artwork", active=False)`
- `MetadataService._on_sync_complete` → spawns enrichment task → finally-block emits `POST_SYNC_PHASE_CHANGED(phase="metadata", active=False)`

[`_on_post_sync_phase`](py_modules/unifideck/core/sync_service.py#L462) discards the phase from `_post_sync_pending`. When the set is empty: `mark_complete()` → status="complete", progress=100% → frontend hides the bar.

### 9.2 What breaks the chain

**Bug 9-A: Empty-games early-return in MetadataService** ([metadata_service.py:96-97](py_modules/unifideck/services/metadata_service.py#L96-L97))

```python
games = kwargs.get("games", [])
if not games:
    return
```

If sync produces zero games (all stores disabled, all stores failed, all games already enriched & deduped to empty), the handler exits without ever emitting `POST_SYNC_PHASE_CHANGED(metadata, active=False)`. `_post_sync_pending` retains `"metadata"` forever. `mark_complete()` never fires.

**Bug 9-B: Three early-returns in ArtworkService** ([event_handlers.py:174, 181, 193](py_modules/unifideck/services/artwork/event_handlers.py#L174))

```python
if not games:                    # line 174
    return
if not grid_dir:                 # line 178-181 — _grid_dir unset
    logger.warning(...)
    return
if not tasks:                    # line 192-193
    return
```

Same shape: any of these returns skips the `_on_artwork_batch_done` callback that would emit the phase-complete event. `_post_sync_pending` keeps `"artwork"`.

**Bug 9-C: Exception inside enrichment loop swallows the emit** ([metadata_service.py:107-145](py_modules/unifideck/services/metadata_service.py#L107))

The `for done, game in enumerate(games, start=1):` loop catches exceptions per-game. The `await self._bus.emit(POST_SYNC_PHASE_CHANGED, ...)` at line 142-145 sits _after_ the loop, at function level. If an exception escapes the per-game handlers (e.g. the bus itself raises, or `asyncio.CancelledError` propagates), the emit never runs. No try/finally guards it.

**Bug 9-D: Artwork phase emits `start_metadata(artwork_total)`** ([sync_service.py:478-479](py_modules/unifideck/core/sync_service.py#L478-L479))

```python
elif phase == "artwork":
    self._progress.artwork_synced = total
    self._progress.start_metadata(total, "sync.extractingMetadata")
```

When the artwork phase ends, this calls `start_metadata(total=artwork_count)`. But metadata runs over _all_ games, not just games that needed artwork. So `metadata_total` is set too low. When MetadataService then ticks `metadata_synced` past `metadata_total`, progress overshoots — and worse, `_recalc()` at [sync_progress.py:168-170](py_modules/unifideck/core/sync_progress.py#L168-L170) computes `sub = synced/total` which can exceed 1.0, producing a percentage > 98%.

**Bug 9-E: `metadata_total` not authoritatively owned**

`MetadataService._run_enrichment` doesn't call `progress.start_metadata(total)` with its own count. It depends on `_on_post_sync_phase` setting it from the artwork side (which is wrong per 9-D), or on `to_dict()`'s fallback to whatever's there. There's no point in the metadata flow where someone says "metadata processes N games" with N being the real count.

**Bug 9-F: Frontend hides on `syncing=false` but progress stays mid-bar**

The frontend ([SyncContext.tsx:85-96](src/contexts/SyncContext.tsx#L85)) deliberately doesn't `setSyncing(false)` on `SYNC_COMPLETE` — it waits for the 500ms poll to read `syncing=false` from `get_sync_progress`. But `get_status()` returns `syncing = self._lock.locked()`, and the lock releases the moment `_finalize_sync` returns. So:

- `syncing=true` for ~2-5 seconds during fetch + dedup
- `_finalize_sync` returns → lock released → backend reports `syncing=false` within 500ms
- Frontend hides bar
- BUT: the bar's last rendered state was `status="artwork"` at 60% (because mark_complete never fired)
- User sees the bar flicker, then disappear without ever showing "complete"

For _that_ user experience to look like "sync doesn't close immediately", the bar may also be **stuck at a high % because the lock didn't release** — that happens when:

- `_lock` is released but the next sync RPC is rejected because some other lock (e.g. `_request_lock` we'd add in PR 3) is still held
- OR the `set_sync_progress(None)` call at [sync_service.py:486](py_modules/unifideck/core/sync_service.py#L486) is gated on `_post_sync_pending` being empty, which it isn't due to 9-A/9-B/9-C

### 9.3 Fix plan

Add a new gap and a new PR.

🔴 **G32** — Post-sync hooks have multiple skip-paths that strand `_post_sync_pending`, leaving sync without a clean completion signal.

#### PR 10 — Cancel-safe post-sync completion

**G32.1** Wrap the entire MetadataService `_run_enrichment` body in a try/finally that _always_ emits `POST_SYNC_PHASE_CHANGED(phase="metadata", active=False)` on exit (success, exception, cancellation):

```python
async def _run_enrichment(self, games: list[Game]) -> None:
    total = len(games)
    try:
        for done, game in enumerate(games, start=1):
            ...
    finally:
        await self._bus.emit(
            Events.POST_SYNC_PHASE_CHANGED,
            phase="metadata", active=False, total=total, done=total,
        )
```

Move the `if not games: return` check **inside** the function, after the totals are emitted — so a zero-game sync still ticks the phase as done:

```python
async def _on_sync_complete(self, **kwargs):
    games = kwargs.get("games", [])
    self._enrichment_task = asyncio.create_task(
        self._run_enrichment(games), name="metadata-enrichment",
    )

async def _run_enrichment(self, games):
    try:
        total = len(games)
        if not games:
            return
        # ... real work ...
    finally:
        await self._bus.emit(POST_SYNC_PHASE_CHANGED, phase="metadata", active=False, total=total, done=total)
```

**G32.2** Same try/finally pattern for ArtworkService — refactor the early-return branches in `_on_sync_complete` so a phase-done event is always emitted, even on skip:

```python
async def _on_sync_complete(self, **kwargs):
    games = kwargs.get("games", [])
    grid_dir = getattr(self, "_grid_dir", None)
    bus = getattr(self, "_bus", None)
    try:
        if not games or not grid_dir:
            return  # but finally still emits
        ...
    finally:
        if bus is not None:
            await bus.emit(POST_SYNC_PHASE_CHANGED, phase="artwork", active=False, total=len(games), done=0)
```

**G32.3** Fix the cross-wired `start_metadata(artwork_total)` in `_on_post_sync_phase`. The metadata service should call `progress.start_metadata(len(games))` from inside `_run_enrichment` _before_ the loop — owning its own total. Remove [sync_service.py:478-479](py_modules/unifideck/core/sync_service.py#L478-L479) entirely.

**G32.4** Add a safety net in `SyncService`: a 10-second watchdog after `_finalize_sync` that, if `_post_sync_pending` still has entries, logs a warning and forces `mark_complete()`. Prevents indefinite stuck-bar even if a future bug regresses this fix:

```python
async def _post_sync_watchdog(self):
    await asyncio.sleep(POST_SYNC_WATCHDOG_SECONDS)  # 30s in config
    if self._post_sync_pending:
        logger.warning(
            "[SyncService] post-sync watchdog tripped: %s never completed",
            sorted(self._post_sync_pending),
        )
        self._progress.mark_complete()
        self._post_sync_pending.clear()
        self._bus.set_sync_progress(None)
```

Spawn it as a background task at the end of `_finalize_sync`.

**G32.5** Frontend: in `SyncContext.tsx`, also listen for `POST_SYNC_PHASE_CHANGED(active=False)` for both phases and clear `isSyncing` when both have reported. Belt-and-suspenders alongside the existing poll-based `syncing=false` detection.

### 9.4 Verification

Manual test cases — each should end with progress bar at 100% then hidden within 1 second:

1. **Normal sync** with 100+ games across stores. Baseline.
2. **All stores disabled** in config → trigger sync → `games=[]` → both phase events still fire, mark_complete fires immediately.
3. **All games already have artwork** → ArtworkService's tasks all return `"cover-exists"` early → still emits phase-done.
4. **Cancel mid-artwork** → cancellation propagates → finally-blocks emit phase-done → mark_cancelled (not mark_complete) fires.
5. **MetadataService internal exception** (induce one by pointing UnifiDB at a bad URL) → finally-block still emits → mark_complete fires.
6. **Single-store refresh after a full sync** → `_post_sync_pending` should be reset at the start of each sync (`sync_single_store` currently doesn't init it — fix that as part of G32).

---

## 10. Architectural constraints (apply to every gap fix)

These are the standards already in force in `for-pr-0.7`. Every closure of a gap must respect them — porting staging code verbatim is NOT acceptable.

### 10.1 File size: 550 LOC cap

Documented in [py_modules/unifideck/core/sync_dedup_mixin.py:5-6](py_modules/unifideck/core/sync_dedup_mixin.py#L5-L6) and enforced by CI volumetry check. When a file approaches the cap, split by concern using mixins or sub-modules. Examples already in the codebase:

- `core/sync_service.py` + `core/sync_dedup_mixin.py` + `core/sync_queries_mixin.py` — same class, three files.
- `services/shortcut/` — package with `events.py`, `reconcile_phases.py`, `games_map_mixin.py`, `launch_options.py`, `registry.py` instead of one shortcut_manager.py.
- `services/microsoft_subscription/` — `service.py` + `cache.py` + `cache_mixin.py` + `event_handlers.py` + `probe_emission.py` + `time_utils.py`.

This is why Phase H ships SGDB as a package, not a single file.

### 10.2 Service pattern

Every new service follows the convention in [services/bootstrap/service_defs.py](py_modules/unifideck/services/bootstrap/service_defs.py):

- Class with `start()` and `stop()` lifecycle methods.
- Subscribes to bus events via `@subscribe(Events.X)` + `auto_wire(self, bus)` in `__init__`.
- Registered in `service_defs.py` so the bootstrap container instantiates it.
- Constructor takes `cache: CacheManager`, `bus: EventBus`, plus its own collaborators — no globals.

When a phase needs new post-sync work (compat fetcher, size fetcher), it gets its own service in this shape, not a flag added to an existing service.

### 10.3 Event-driven, never direct calls

Cross-service coordination goes through `EventBus`. Examples already in place:

- ShortcutService listens for `SYNC_COMPLETE` and emits `SHORTCUT_RECONCILE_COMPLETE`.
- ArtworkService listens for `GAME_INSTALLED`, `SHORTCUT_CREATED`, `ARTWORK_REQUEST`, `SYNC_COMPLETE`.
- MetadataService listens for `SYNC_COMPLETE`.

When a new event is needed, add it to [core/types/events.py](py_modules/unifideck/core/types/events.py) — don't reach across services with direct method calls.

### 10.4 Mypy-strict, ruff-clean

The project uses strict mypy + ruff. All new code must:

- Have explicit type annotations on every parameter and return.
- Use `from __future__ import annotations` at the top of every module.
- Avoid `Any` except for `**kwargs: Any` on event handlers (the bus is loosely typed by design).
- Follow ruff's import ordering and line-length rules — no manual disables.

### 10.5 Logging convention

`[ServiceName] short message` for INFO, `[ServiceName.submodule] detail` for DEBUG. See [services/artwork/event_handlers.py:55-57](py_modules/unifideck/services/artwork/event_handlers.py#L55-L57) for the pattern. Greppable prefixes are non-negotiable — the user reads logs in `~/homebrew/logs/Unifideck/*.log` to diagnose runtime issues.

### 10.6 No new top-level dependencies without justification

`requirements.in` is curated. Anything beyond `aiohttp` for HTTP or the existing `steamgrid` package is a conversation. The SGDB port should use the same `steamgrid` library currently imported — don't shop for a new SGDB client.

### 10.7 Docstrings: why, not what

Every public method gets a docstring explaining the _reason_ the function exists (constraints, gotchas, why this approach over alternatives). Don't restate the signature. See [services/metadata_service.py:75-94](py_modules/unifideck/services/metadata_service.py#L75-L94) — that docstring explains why enrichment must be a fire-and-forget task, including the specific deadlock it prevents. Match that quality.

### 10.8 Frontend: SyncContext is the single source of truth

The frontend's progress UI reads from `SyncContext` ([src/contexts/SyncContext.tsx](src/contexts/SyncContext.tsx)). Don't fan out parallel state — when a new counter is added, it goes into the context, exposed via the `useSync()` hook, and consumed by components.

i18n keys go in `src/i18n/locales/en-US.json` (and all 13 sibling locales). Backend-emitted keys must be added to **every locale** as part of the same PR — partial locale support is a regression.
