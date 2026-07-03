# Ubisoft Connect Store Integration -- Implementation Tracker

> **🗄️ ARCHIVED (2026-06-22) — outdated.** A phase tracker referencing pre-refactor file
> paths (`stores/ubisoft_api.py`, `stores/ubisoft.py`) that no longer exist — the
> integration is now the `stores/ubisoft/` package. For the current design see the v2 spec:
> [`../ubisoft-store-spec.md`](../ubisoft-store-spec.md).

**Spec (current):** [`../ubisoft-store-spec.md`](../ubisoft-store-spec.md)
**Started:** 2026-03-09
**Status:** Phase 1 ✅ | Phase 2 ✅ | Phase 3 ✅ | Phase 4 ✅

---

## Phase 1: Auth + Library + Steam Shortcuts (MVP)

**Goal:** Users can sign in, see their Ubisoft library, and have Steam shortcuts written on sync.

| #   | Task                                                                                    | Files                                                                                                                                                                           | Status            |
| --- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| 1A  | Create `ubisoft_api.py` -- auth flow, token management, GraphQL client                  | `py_modules/unifideck/stores/ubisoft_api.py`                                                                                                                                    | DONE              |
| 1B  | Create `ubisoft.py` connector -- `UbisoftConnector(Store)` with auth + library          | `py_modules/unifideck/stores/ubisoft.py`                                                                                                                                        | DONE              |
| 1C  | Wire into `main.py` -- register store, RPC methods, sync integration, download callback | `main.py`                                                                                                                                                                       | DONE              |
| 1D  | Update `launch_options_parser.py` -- add `ubisoft` to regex + add tests                 | `py_modules/unifideck/shortcuts/launch_options.py`, `tests/test_launch_options_parser.py`                                                                                       | DONE              |
| 1E  | Frontend -- add `ubisoft` to Store type, StoreConnections, tabs, index.tsx handlers     | `src/types/store.ts`, `src/components/settings/StoreConnections.tsx`, `src/tabs/TabContainer.ts`, `src/tabs/filters.ts`, `src/index.tsx`, `src/components/UbisoftAuthModal.tsx` | DONE              |
| 1F  | Add i18n keys for all Ubisoft strings                                                   | `src/i18n/locales/en-US.json`                                                                                                                                                   | DONE              |
| 1G  | Update stores `__init__.py` to export `UbisoftConnector`                                | `py_modules/unifideck/stores/__init__.py`                                                                                                                                       | DONE              |
| 1H  | Add launch options parser tests for `ubisoft:` prefix                                   | `tests/test_launch_options_parser.py`                                                                                                                                           | DONE (45/45 pass) |

### Phase 1 Exit Criteria

- [x] User can sign in to Ubisoft Connect from Unifideck settings (email/password + 2FA)
- [x] Ubisoft library tab shows owned PC games with cover art
- [x] Token refresh works automatically
- [x] Logout works and clears all state
- [x] Library tab hidden when no games
- [x] Steam shortcuts written to `shortcuts.vdf` on first sync (tag `Not Installed`)
- [x] `launch_options_parser.py` correctly parses `ubisoft:{space_id}` (unit tests pass)
- [x] Steam Grid artwork downloaded for all owned games after sync
- [x] Template prefix created as background task on first successful sync

---

## Phase 2: Client Bootstrap + Install + Launch

**Goal:** Users can install and launch Ubisoft games.

| #   | Task                                                                                            | Files                                           | Status |
| --- | ----------------------------------------------------------------------------------------------- | ----------------------------------------------- | ------ |
| 2A  | Create `ubisoft_parser.py` -- binary configurations/ownership parser                            | `py_modules/unifideck/stores/ubisoft_parser.py` | DONE   |
| 2B  | Create `bin/ubisoft_setup.py` -- per-game prefix bootstrap (download/install or clone template) | `bin/ubisoft_setup.py`                          | DONE   |
| 2C  | Create `bin/ubisoft_set_language.py` -- Wine registry language setter                           | `bin/ubisoft_set_language.py`                   | DONE   |
| 2D  | Download manager integration -- add `_download_ubisoft()` + dispatch + error patterns           | `py_modules/unifideck/download/manager.py`      | DONE   |
| 2E  | Launcher script integration -- add `ubisoft` case + launch section                              | `bin/unifideck-launcher`                        | DONE   |
| 2F  | Wire download completion callback for Ubisoft in `main.py`                                      | `main.py`                                       | DONE   |
| 2G  | Implement `install_game()`, `uninstall_game()`, `update_game()` in connector                    | `py_modules/unifideck/stores/ubisoft.py`        | DONE   |
| 2H  | Update build script to include new Ubisoft files                                                | `build-plugin_old.sh`                           | DONE   |

### Phase 2 Exit Criteria

- [x] Ubisoft Connect client auto-installs on first use (template clone or fresh install)
- [x] Games install via download queue with progress indication
- [x] Games launch via `uplay://` protocol through `upc.exe`
- [x] Uninstall works (protocol or direct delete)
- [x] Installed status correctly detected
- [x] Language settings applied

---

## Phase 3: Polish + Edge Cases

**Goal:** Download tracking, compat data, games.map, logging, SD card, prefix repair, and bug fixes.

| #   | Task                                                                                                    | Files                                                            | Status       |
| --- | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------ |
| 3A  | SD card install support — `SDCARD_INSTALL_BASE`, `install_path` param, detection scan                   | `ubisoft.py`, `manager.py`, `main.py`                            | DONE         |
| 3B  | Repair Ubisoft Connect prefix — `repair_prefix()` method + RPC                                          | `ubisoft.py`, `main.py`, `en-US.json`                            | DONE         |
| 3C  | Download tracking — speed estimation, ETA smoothing, size cache; `--` for unknown size/% on 1st install | `manager.py`, `ubisoft.py`                                       | DONE         |
| 3D  | ProtonDB/Deck Verified — add `'ubisoft'` to compat fetcher store filter                                 | `compat/library.py`, `protondb.ts`                               | DONE         |
| 3E  | Games.map reconciliation — add ubisoft to `reconcile_games_map_from_installed`                          | `shortcuts_manager.py`, `main.py`                                | DONE         |
| 3F  | i18n — Ubisoft download error keys, prefix repair toasts                                                | `en-US.json`                                                     | DONE         |
| 3G  | Base.py docstrings — add 'ubisoft' to Store/Game comments                                               | `base.py`                                                        | DONE         |
| 3H  | Auth bug fixes — 2FA ticket storage, `requires_2fa` key mismatch, `start_auth` routing                  | `ubisoft.py`, `main.py`, `UbisoftAuthModal.tsx`                  | DONE         |
| 3I  | Proton settings — verified existing infra works for Ubisoft (store-agnostic)                            | (no changes needed — `save_proton_setting` uses `store:game_id`) | DONE         |
| 3J  | Download tracking spec update — document FS polling strategy + comparison table                         | `ubisoft-store-spec.md`                                          | DONE         |
| 3K  | Handle Ubisoft+ subscription games (listed normally in library views)                                   | `ubisoft_api.py`                                                 | Not Required |
| 3L  | Proactive update checking (optional)                                                                    | `ubisoft.py`                                                     | Not Required |
| 3M  | Cross-store dedup (Epic-bought Ubisoft games)                                                           | `ubisoft.py`, `epic.py`                                          | Not Required |

### Phase 3 Exit Criteria

- [x] Download speed and ETA displayed in UI (FS-based estimation with EMA smoothing)
- [x] Size cache updated after first install (subsequent installs have accurate progress %)
- [x] Ubisoft games get ProtonDB/Deck Verified ratings
- [x] games.map reconciliation includes Ubisoft games
- [x] SD card installs work via download queue storage location selection
- [x] Prefix repair available via RPC (`repair_ubisoft_prefix`)
- [x] Auth 2FA flow works end-to-end (ticket stored between login and 2FA calls)
- [x] Proton compatibility tool settings work for Ubisoft games
- [x] Ubisoft-specific download error messages in UI
- [x] Download tracking strategy documented in spec (§11.3.1)

---

## Phase 4: Install UX + Auth Token Propagation

**Goal:** Ubisoft-specific install confirmation modal and end-to-end auth token propagation from first UPC login through all prefixes.

| #   | Task                                                                                           | Files                                                        | Status |
| --- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ------ |
| 4A  | Ubisoft-specific install confirmation modal — "This will load the Ubisoft Connect Launcher..." | `gameActionInterceptor.ts`, `PlayButtonOverride.tsx`         | DONE   |
| 4B  | i18n keys for Ubisoft install modal (ubisoftInstallTitle, ubisoftInstallDescription, etc.)     | `en-US.json`                                                 | DONE   |
| 4C  | Auth token propagation after install — capture UPC token → template → all existing prefixes    | `ubisoft.py` (`_install_via_upc_ui`, `_capture_upc_session`) | DONE   |
| 4D  | Update spec & tracker docs with Phase 4 approach notes                                         | `ubisoft-store-spec.md`, `ubisoft-implementation-tracker.md` | DONE   |

### Phase 4 Exit Criteria

- [x] Clicking Install on a Ubisoft game shows store-specific modal: "This will load the Ubisoft Connect Launcher which you will need to install {{title}}."
- [x] Confirm button reads "Open Ubisoft Connect" (not generic "Yes")
- [x] After user installs game via UPC, button transitions Install → Play
- [x] Launch uses `upc.exe uplay://launch/{launch_id}/0` (same mechanism as Epic-Ubisoft)
- [x] First UPC login token captured → saved to session file → written to template → propagated to all game prefixes
- [x] Future game prefixes (cloned from template) inherit the auth token automatically

### Phase 4 Approach Notes

**Install Flow (updated):**

1. User clicks Install → Ubisoft-specific ConfirmModal appears (not the generic one)
2. On confirm → `add_to_download_queue(store="ubisoft")` → `_download_ubisoft()` → `install_game()`
3. UPC opens visibly in the game's per-game prefix with pre-injected REST API ticket
4. If first-time login: user authenticates in UPC; token captured after UPC exits
5. User installs game through UPC UI; backend monitors filesystem for completion
6. On completion: marker written, shortcut registered, cache updated, button → Play

**Auth Token Flow (updated):**

- **First install**: REST API ticket pre-injected → UPC may accept it or prompt manual login → after UPC exits, `_capture_upc_session()` reads UPC's native `restore_session` token → saved to `UPC_SESSION_FILE` → written to `.template/` settings.yml → `_propagate_upc_session_to_all_prefixes()` copies to all existing per-game prefixes
- **Subsequent installs**: Cloned from template (which has the captured token) → UPC auto-logs in → no manual login needed
- **Key fix**: `_install_via_upc_ui()` now propagates captured tokens to all existing prefixes (previously only wrote to template + session file but did not propagate)

**Launch Mechanism (unchanged):**

- Both native Ubisoft and Epic-Ubisoft use identical protocol: `upc.exe uplay://launch/{id}/0`
- Native uses per-game prefix at `~/.local/share/unifideck/prefixes/ubisoft/{space_id}/`
- Epic-Ubisoft uses Epic's prefix with UPC found therein
- Same `PROTON_VERB=waitforexitandrun` and `umu-run` wrapper

---

## Implementation Notes

### Key Architecture Decisions

- **Per-game prefix model**: Each game gets `~/.local/share/unifideck/prefixes/ubisoft/{space_id}/`
- **No CLI binary**: Direct REST/GraphQL API for auth + library; `upc.exe` inside Wine for install/launch
- **Auth via native form**: Email/password fields in Decky settings panel (no browser popup)
- **Template prefix**: Created as background task during first successful sync, cloned for subsequent games
- **Separate from Epic SSO path**: New code path, does NOT touch L1333-1470 in unifideck-launcher

### Reference Implementations

- `AmazonConnector` in `py_modules/unifideck/stores/amazon.py` -- primary pattern reference
- `amazon_set_language.py` in `bin/` -- language setter pattern
- `GOGAPIClient` token refresh -- token management pattern

### Files Created

- `py_modules/unifideck/stores/ubisoft_api.py` -- REST/GraphQL API client (~460 lines)
- `py_modules/unifideck/stores/ubisoft.py` -- UbisoftConnector(Store) (~1060 lines, expanded in Phase 2)
- `py_modules/unifideck/stores/ubisoft_parser.py` -- Binary configurations/ownership parser (~310 lines)
- `bin/ubisoft_setup.py` -- Per-game prefix bootstrap: template clone or fresh install (~370 lines)
- `bin/ubisoft_set_language.py` -- Wine registry language setter (~220 lines)
- `src/components/UbisoftAuthModal.tsx` -- Credentials + 2FA auth modal
- `docs/ubisoft-implementation-tracker.md` -- This file

### Files Modified

- `py_modules/unifideck/stores/__init__.py` -- Added UbisoftConnector export
- `py_modules/unifideck/shortcuts/launch_options.py` -- Added `ubisoft` to regex pattern
- `main.py` -- Import, init, check_store_status, auth RPCs, sync, force_sync, download callback, ubisoft install callback wiring
- `src/types/store.ts` -- Added `"ubisoft"` to Store type
- `src/components/settings/StoreConnections.tsx` -- Added ubisoft to STORES array
- `src/tabs/TabContainer.ts` -- Added Ubisoft tab (position 7), tab visibility (hide when 0 games), ubisoft game counting
- `src/tabs/filters.ts` -- Added `"ubisoft"` to all store union types
- `src/index.tsx` -- storeStatus, checkStoreStatus, pollForAuthCompletion, startAuth (modal), handleLogout
- `src/i18n/locales/en-US.json` -- Added ubisoftConnect, ubisoftAuth, launcher toast keys
- `tests/test_launch_options_parser.py` -- Added 8 ubisoft test cases (45/45 pass)
- `py_modules/unifideck/download/manager.py` -- Added `_download_ubisoft()`, ubisoft routing, callback setter, upc.exe cleanup, error patterns
- `bin/unifideck-launcher` -- Added `ubisoft` store case + ~130-line launch section (separate from Epic SSO)
- `build-plugin_old.sh` -- Added 5 Ubisoft files to CRITICAL_FILES array
- `py_modules/unifideck/compat/library.py` -- Added `'ubisoft'` to BackgroundCompatFetcher store filter (Phase 3)
- `py_modules/unifideck/shortcuts/shortcuts_manager.py` -- Added ubisoft to `reconcile_games_map_from_installed()` (Phase 3)
- `py_modules/unifideck/stores/base.py` -- Added 'ubisoft' to Game/Store docstrings (Phase 3)
- `src/tabs/protondb.ts` -- Updated comment to include Ubisoft (Phase 3)
- `docs/ubisoft-store-spec.md` -- Added §11.3.1 Download Status Tracking Strategy (Phase 3)

---

## Change Log

| Date       | Phase | Changes                                                                                                                            |
| ---------- | ----- | ---------------------------------------------------------------------------------------------------------------------------------- |
| 2026-03-09 | Setup | Created implementation tracker                                                                                                     |
| 2026-03-09 | 1A    | Created `ubisoft_api.py` -- complete auth + token + GraphQL library client                                                         |
| 2026-03-09 | 1B    | Created `ubisoft.py` -- UbisoftConnector with all Store ABC methods                                                                |
| 2026-03-09 | 1C    | Wired into main.py -- import, init, status, auth RPCs, sync, force_sync, download callback                                         |
| 2026-03-09 | 1D    | Updated launch_options_parser regex + docstring                                                                                    |
| 2026-03-09 | 1E    | Frontend: Store type, StoreConnections, tabs, filters, index.tsx auth handlers, UbisoftAuthModal                                   |
| 2026-03-09 | 1F    | Added all i18n keys (ubisoftConnect, ubisoftAuth section, launcher toast)                                                          |
| 2026-03-09 | 1G    | Exported UbisoftConnector from stores **init**.py                                                                                  |
| 2026-03-09 | 1H    | Added 8 ubisoft parser test cases, all 45 tests pass                                                                               |
| 2026-03-09 | 1-fix | Fixed Phase 1 remainders: tab visibility (hide when 0 games), template prefix (already done)                                       |
| 2026-03-09 | 2A    | Created `ubisoft_parser.py` -- varint decoder, GameConfig, configurations/ownership parsers                                        |
| 2026-03-09 | 2B    | Created `bin/ubisoft_setup.py` -- prefix bootstrap with template clone + fresh install paths                                       |
| 2026-03-09 | 2C    | Created `bin/ubisoft_set_language.py` -- Wine registry locale + UPC language setter                                                |
| 2026-03-09 | 2D    | Download manager: `_download_ubisoft()`, routing, callback, upc.exe cleanup, error patterns                                        |
| 2026-03-09 | 2E    | Launcher script: `ubisoft` case + full launch section with prefix env, uplay:// protocol                                           |
| 2026-03-09 | 2F    | Wired ubisoft install callback in main.py                                                                                          |
| 2026-03-09 | 2G    | Implemented `install_game()`, `uninstall_game()`, `update_game()` in ubisoft.py (replaced stubs)                                   |
| 2026-03-09 | 2H    | Updated `build-plugin_old.sh` CRITICAL_FILES with 5 new Ubisoft files                                                              |
| 2026-03-09 | 3A    | SD card install: `SDCARD_INSTALL_BASE`, `install_path` param, dual-base detection scan                                             |
| 2026-03-09 | 3B    | Prefix repair: `repair_prefix()` method + `repair_ubisoft_prefix` RPC + toast keys                                                 |
| 2026-03-09 | 3C    | Download tracking: speed estimation (byte delta / time), EMA-smoothed ETA (α=0.1→0.3), size cache                                  |
| 2026-03-09 | 3D    | ProtonDB/Deck Verified: added `'ubisoft'` to BackgroundCompatFetcher store filter                                                  |
| 2026-03-09 | 3E    | Games.map reconciliation: added ubisoft branch to `reconcile_games_map_from_installed()`                                           |
| 2026-03-09 | 3F    | i18n: added `ubisoftClient`, `ubisoftBootstrap`, `ubisoftUmuMissing` error keys + repair toasts                                    |
| 2026-03-09 | 3G    | Base.py: updated Game dataclass store comment + Store ABC docstring                                                                |
| 2026-03-09 | 3H    | Auth fixes: 2FA ticket storage, `requires_2fa` key fix, `start_auth` routing fix                                                   |
| 2026-03-09 | 3I    | Proton settings: verified store-agnostic infra works for `ubisoft:{space_id}` — no code needed                                     |
| 2026-03-09 | 3J    | Spec update: added §11.3.1 download tracking strategy with comparison table + fallback docs                                        |
| 2026-03-09 | 3J    | Spec update: documented that no API/metadata/3rd-party source provides Ubisoft install sizes; `--` for size/ETA/% on first install |
| 2026-03-09 | Bug   | Fixed pfx/ path detection for configurations binary + upc.exe (Proton creates pfx/ subdirectory)                                   |
| 2026-03-09 | Bug   | Fixed os.environ.copy() in install/uninstall/update — replaced with clean env builder (prevents Steam env interference)            |
| 2026-03-09 | Bug   | Fixed userId key mismatch in bin/ubisoft_setup.py (snake_case → camelCase)                                                         |
| 2026-03-09 | Bug   | Added DISPLAY/WAYLAND_DISPLAY pass-through to \_build_umu_env (UPC needs display to run)                                           |
| 2026-03-09 | Feat  | Added static game ID database lookup (Tier 2) — resolves ~90% of install_ids without needing UPC to run                            |
| 2026-03-09 | Feat  | Added manual UPC install fallback (Path B) — launches authenticated UPC when install_id unavailable, monitors FS for new installs  |
| 2026-03-09 | Docs  | Updated spec §5.4 (3-tier install_id resolution), §6.3 (Path A/B install flows), §16.2 (error handling), §14.1 (data files)        |
| 2026-03-09 | 4A    | Added Ubisoft-specific install confirmation modal in gameActionInterceptor.ts + PlayButtonOverride.tsx                             |
| 2026-03-09 | 4B    | Added i18n keys: ubisoftInstallTitle, ubisoftInstallDescription, ubisoftInstallConfirm                                             |
| 2026-03-09 | 4C    | Fixed auth token propagation: \_install_via_upc_ui now propagates captured token to all existing prefixes (was template-only)      |
| 2026-03-09 | 4D    | Updated spec §6.3 + §7.5 with Ubisoft install modal + auth capture flow; added Phase 4 to tracker                                  |
