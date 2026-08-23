# Manual Install — the `manual` store

> Status: implemented on top of the official **0.7.4** base (`cb2eeaa`),
> branch `claude/unifideck-manual-install-m0bjod`, commit `21651dd`.
> Versión en castellano: [manual-install.es.md](./manual-install.es.md).
>
> Lets the user install games from an installer `.exe`/`.msi` they already
> have locally: a Proton prefix is created, the installer runs inside
> gamescope, the game files land **outside the prefix** (drive `D:`), and
> once it finishes the Steam shortcut is created with unifiDB/SteamGridDB
> artwork and metadata. Manual games show up in the Downloads tab so they
> can be uninstalled.

---

## 1. Design idea

Everything rides on Unifideck's existing foundation; there is no new
launching, prefix, or artwork machinery:

| Need | Existing piece reused |
|---|---|
| Register the store | `StoreRegistry` auto-discovery (`stores/manual/manual_store.py` is enough) |
| Launch an arbitrary exe under umu/Proton | `generic_launch → _raw_exe_launch` (an unknown store falls through to it, with `STORE=none` for umu) |
| Create the prefix with the default Proton | `setup_prefix` (the same canonical path a normal launch takes) |
| Open a window in Gaming Mode | The wrapper stores' RunGame dance (`wrapper-shortcut-launch.ts` + temporary `AddShortcut`) — a bare backend subprocess has **no gamescope session** and its window never appears |
| Deterministic AppID + shortcut + `games.map` | `generate_app_id(launcher, "manual:<id>")`, the `ensure_auth_shortcut` pattern, `mark_installed` |
| Metadata and artwork | The normal sync phases: unifiDB **by title**, SteamGridDB, Steam CDN |
| Uninstall from Downloads | `uninstall_game(app_id)` → `registry.get_store("manual").uninstall_game()` |

Key decisions:

1. **Game files live outside the prefix.** The prefix
   (`~/.local/share/unifideck/prefixes/<game_id>`) is disposable: it can be
   regenerated or switched to another Proton without losing the game. Wine
   exposes the game's real folder as drive `D:` through a symlink at
   `<prefix>/dosdevices/d:` → `~/Games/Manual/<game_id>`. The mapping is
   re-ensured **on every launch** (idempotent), so it survives a prefix
   regeneration.

2. **"Install by playing", with no user intervention.** The shortcut is
   created first with `games.map` pointing at the **installer**; the frontend
   RunGames it automatically when the backend's event arrives. The wizard
   runs under Steam/gamescope with Steam Input and the on-screen keyboard
   working. When the process exits, the user is asked for the real exe and
   `games.map` is re-pointed.

3. **A store with no authentication.** `store_info.auth_method = "none"` —
   the flag already existed on `StoreInfo` with no consumers; now
   `StoreConnections` uses it to skip the Authenticate button. All of the
   store's auth methods are honest no-ops.

---

## 2. How the flow works (end to end)

```
Settings → MANUAL INSTALL → "Select exe"
  │  openFilePicker (.exe/.msi), starts at $HOME
  ▼
ManualInstallTitleModal  (title pre-filled from the file name)
  │  RPC manual_install_start(installer_path, title)
  ▼
BACKEND (ManualInstallRPCMixin):
  1. validates installer and title; derives game_id = slug(title)-crc32(path)
  2. creates ~/Games/Manual/<game_id>/
  3. stores the record {status: "installing"} in manual_games.json
  4. writes the shortcut into shortcuts.vdf (LaunchOptions "manual:<id>")
     + games.map row: exe = INSTALLER, work_dir = game folder
  5. emits MANUAL_INSTALL_LAUNCH_REQUESTED {store_game_id}
  6. emits ARTWORK_REQUEST (artwork starts downloading already, by title)
  7. queues a background sync (Downloads + unifiDB metadata)
  ▼
FRONTEND (manual-install-listener, lives outside the QAM):
  8. get_compat_tool_for_game("manual:<id>") → appid, launcher_path…
  9. RunGame of the shortcut (if Steam doesn't have it in memory yet: a
     TEMPORARY shortcut via AddShortcut — first run after the vdf write)
  ▼
LAUNCHER (bin/unifideck-launcher process):
 10. setup_prefix creates/validates the prefix with the default Proton
 11. ensure_manual_drive: dosdevices/d: → ~/Games/Manual/<game_id>
 12. _raw_exe_launch runs the INSTALLER under umu/Proton
     → the user completes the wizard picking drive D:
  ▼
FRONTEND:
 13. watchAppStopped detects the app ended
 14. manual_install_status → still "installing" → ManualInstallExeModal
 15. the user picks the game's .exe (picker starts in the D: folder)
     │  RPC manual_install_finalize(game_id, exe_path)
  ▼
BACKEND:
 16. validates the exe (confined to the game folder or its prefix)
 17. record → {status: "ready", exe_path}; if it was installed onto C:,
     install_path is re-anchored on the exe's directory
 18. writes .unifideck_manifest.json (discovery)
 19. mark_installed re-points games.map at the real exe
 20. emits GAME_INSTALLED + background sync (remaining metadata/artwork)
  ▼
FRONTEND:
 21. "game ready" toast + SteamRestartModal (the tile only appears once
     Steam re-reads shortcuts.vdf)
```

**Subsequent Play**: Steam → `unifideck-launcher manual:<id>` → `games.map`
resolves the exe → prefix `prefixes/<id>` → `generic_launch` under
umu/Proton.

**Uninstall** (Downloads tab): `uninstall_game(app_id)` →
`ManualStore.uninstall_game` deletes the game folder (guarded rmtree: never
`/`, never `$HOME`, depth ≥ 3), optionally the prefix (modal toggle), drops
the record and emits `GAME_UNINSTALLED`. For the manual store, that event's
handler removes the shortcut **entirely** — it is not left as
"Not Installed", because the game no longer exists in any library and its
Install button could not work — and the `SHORTCUT_REMOVED` handler cleans
the `grid/` artwork. The frontend also removes it from Steam's **live
session** via `SteamClient.Apps.RemoveShortcut`, so the tile disappears
immediately (library and Recents) with no restart; only if that live
removal fails is the Steam restart prompt offered.

---

## 3. The `manual` store (backend)

### State — `py_modules/unifideck/stores/manual/state.py`

One JSON file IS the whole library:
`~/.local/share/unifideck/manual_games.json` (configurable via
`stores.manual.state_file`). Atomic writes (tmp + `os.replace`); malformed
rows are dropped with a warning instead of failing the whole load.

```json
{
  "version": 1,
  "games": [
    {
      "game_id": "dark-forest-1a2b3c4d",
      "title": "Dark Forest",
      "installer_path": "/home/deck/Downloads/setup_dark_forest.exe",
      "install_path": "/home/deck/Games/Manual/dark-forest-1a2b3c4d",
      "exe_path": "",
      "status": "installing",   // "installing" | "ready"
      "added_at": 1755960000.0
    }
  ]
}
```

* `status: "installing"` → `get_library()` exposes the game with
  `exe_path = installer_path`: pressing Play re-runs the installer (that IS
  the pending action) and the `games.map` row stays alive across syncs.
* `game_id = slug(title)[:32] + "-" + crc32(installer_path)`: stable
  (re-adding the same installer reuses the record), unique across identical
  titles from different installers, and valid for the identifier regex and
  as the prefix directory name.

### `ManualStore` — `manual_store.py`

The full `StoreBase` contract: `is_available() = True` always; auth no-ops;
`get_library()` returns every record as `Game(installed=True, exe_path,
install_path, metadata.manual_status)`; `install_game/update_game` don't
apply (the download queue is not involved); `get_game_size` measures the
directory; `uninstall_game` as described above. `logout()` is a deliberate
no-op: "log out / clear accounts" must **not** destroy local games.

### The critical data point

`reconcile` only writes a `games.map` row for games that are `installed`
**with** an `exe_path`. The manual store always returns both, so the row is
rewritten on every sync — these games never depend on the DownloadWorker
flow.

---

## 4. RPC — `py_modules/unifideck/rpc/mixins/manual_install.py`

| RPC | What it does |
|---|---|
| `manual_install_start(installer_path, title)` | Steps 1-7 of the flow. Returns `{game_id, app_id, install_path}` |
| `manual_install_finalize(game_id, exe_path)` | Steps 16-20. Confines the exe to the game folder or its prefix (anti-traversal guard) |
| `manual_install_status(game_id)` | The current record (the frontend decides whether to ask for the exe after the app stops) |

The ad-hoc shortcut is written by `stores/manual/shortcut.py`
(`ensure_manual_game_shortcut`): it reads the vdf **from disk** (Steam
flushes over the in-memory copy), appends an entry with the same shape as
reconcile's `_build_shortcut_entry` (same appid, same LaunchOptions), and
the next sync's reconcile **adopts** it instead of duplicating it.

## 5. Drive D: — `stores/manual/drive.py` + launcher hook

`ensure_manual_drive(prefix_root, target_dir)` creates/repoints the
`<prefix>/dosdevices/d:` symlink. It never destroys a real directory
occupying the letter. It is invoked from
`services/launcher/orchestrator.py` (`_ensure_manual_drive_mapping`) right
after `setup_prefix` and before running the game/installer — best-effort:
if it fails, only the drive-letter convenience is lost, never the launch.

## 6. Frontend

| Piece | File |
|---|---|
| Settings section | `src/components/settings/ManualInstallSection.tsx` |
| Title modal | `src/components/modals/ManualInstallTitleModal.tsx` |
| Post-install exe modal | `src/components/modals/ManualInstallExeModal.tsx` |
| RunGame listener (lives outside the QAM) | `src/services/manual-install-listener.tsx` (started in `definePlugin`, stopped in `teardown`) |

The `manual_install_launch_requested` event is in `WATCHED_EVENTS` **and**
in `IMPERATIVE_EVENTS` (it must not re-fire from the replay backlog on
reload — that would relaunch the installer). The picker uses
`openFilePicker` with the `ChangeExecutableModal` contract: no RegExp
`filter` (it cannot cross the JS→Python bridge), `extensions` drives the
filtering.

`ChangeExecutableModal` ("Change executable…" in the context menu) also
works for manual games: `"manual"` is in the executables mixin's
`_DIRECT_LAUNCH_STORES`, so the override IS the `games.map` exe column.

## 7. Closed store lists that were widened

Adding a store touches ~15 closed lists. The ones in this change:

* **Backend**: `services/shortcut/launch_options.py` (`STORE_ID_PATTERN` —
  the #1 silent failure if forgotten: reconcile would no longer recognise
  the shortcuts as ours), `core/types/events.py` (`StoreEnum` + new event),
  `bootstrap/cache_registry.py`, `config/config_manager.py` (fallback),
  `defaults/config.json`, `config/schema.json`, `config/key_presence.py`,
  `utils/paths.py` (`DEFAULT_INSTALL_DIRS`),
  `scripts/validate_event_schemas.py`, `main.py` (mixin).
* **Frontend**: `src/types/api.ts` (`StoreId`, `StoreInfo.auth_method`),
  `src/types/store.ts` (`STORE_VISUALS`), `StoreIcon.tsx` (`FaHdd` icon),
  `src/lib/library-filters/index.ts` (`StoreSlug` + counts),
  `src/lib/steam-bridge/tab-container.ts` ("Manual" tab, visible only with
  ≥ 1 game), `UnifiedLibraryView.tsx` (filter), `rpc-routes.ts`,
  `types/events.ts`, `event-bus-client.ts`.
* **i18n**: `manualInstall.*` block + `deckTabs.manual` in all 16 locales;
  `deckTabs.manual` allowlisted for es-ES/pt-BR ("Manual" matches the
  English spelling by lexical coincidence).

## 8. Known limitations

1. **The library tile appears after restarting Steam** — Steam only reads
   `shortcuts.vdf` at startup. The final modal offers the restart;
   installing and picking the exe work without restarting (temporary
   shortcut).
2. **"Later" on the exe modal** → the record stays `installing`; Play
   re-runs the installer and the modal comes back when it exits.
3. **Installing onto C: instead of D:** works (the picker can navigate into
   the prefix and `install_path` is re-anchored on the exe's directory),
   but the game then lives inside the prefix and deleting the prefix
   deletes it.
4. **Installers that relaunch themselves** (parent exits, a child carries
   on): umu/Proton waits for `wineserver`
   (`PROTON_VERB=waitforexitandrun`), which covers most cases; if the modal
   ever shows early, dismiss it with "Later" and pick the exe once the
   installer is done.
5. Save data inside the prefix is lost when uninstalling with "delete
   prefix" enabled (the modal's standard behaviour).

## 9. Verification

* `ruff`, `mypy`, `tsc`, `eslint`, `prettier`, rollup build, volumetry
  (files/functions/locals/nesting/fanout), `validate_event_schemas`,
  `check_config_keys` and the 4 i18n checks: **green**.
* `tests/unit/test_manual_store.py` (13 tests): state, library mapping,
  uninstall guards, id derivation, finalize confinement, shortcut
  creation/reuse, drive D: mapping, auth contract. Full suite: 2428 pass
  (the remaining battlenet-prefix/AuthDispatcher failures are pre-existing
  local-environment artifacts, present on the clean 0.7.4 base too).

Still to validate on real hardware: the first launch (prefix creation +
wizard opening), D: showing up inside the wizard, and the full cycle
through to playing.
