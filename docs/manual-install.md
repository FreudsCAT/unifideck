# Manual Install — the `manual` store

> Status: **v2**, on top of the official **0.7.4** base (`cb2eeaa`),
> branch `claude/unifideck-manual-install-v2` (v1 is frozen on
> `claude/unifideck-manual-install-v1`).
> Versión en castellano: [manual-install.es.md](./manual-install.es.md).
>
> Lets the user install games from an installer `.exe`/`.msi` they already
> have locally: a Proton prefix is created, the installer runs inside
> gamescope, the game files land **outside the prefix** (drive `U:`), and
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
   exposes the game's real folder as drive `U:` through a symlink at
   `<prefix>/dosdevices/u:` → `~/Games/Manual/<game_id>` (in umu
   prefixes the compat-data root IS the wine prefix — umu creates
   `pfx` as a symlink to `.`). The letter is `U:` ("Unifideck") and not
   `D:` because Wine's mountmgr auto-assigns removable devices upward
   from D — on machines whose SD reader shows up as `/dev/sda` it claims
   `d::` and deletes a foreign `d:` symlink at boot. The mapping is
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
  7. (the library sync is deferred to the END of the run, via
     manual_ensure_shortcut — a mid-flow sync would re-add the row
     Steam just erased and pop the sync's own "restart Steam?" prompt
     at the wrong moment)
  ▼
FRONTEND (manual-install-listener, lives outside the QAM):
  8. get_compat_tool_for_game("manual:<id>") → appid, launcher_path…
  9. RunGame of the shortcut (if Steam doesn't have it in memory yet: a
     TEMPORARY shortcut via AddShortcut — first run after the vdf write)
  ▼
LAUNCHER (bin/unifideck-launcher process):
 10. setup_prefix creates/validates the prefix with the default Proton
 11. ensure_manual_drive: dosdevices/u: → ~/Games/Manual/<game_id>
 12. _raw_exe_launch runs the INSTALLER under umu/Proton
     → the user completes the wizard picking drive U:
  ▼
FRONTEND:
 13. watchAppStopped detects the app ended
 14. manual_install_status → still "installing" → ManualInstallExeModal
 15. the modal LISTS candidate .exes (scan of the U: folder AND the
     prefix's drive_c, filtering installers/redists AND Wine's stock
     content: windows/, iexplore, wmplayer…) — one tap for the common case;
     "Browse…" remains as fallback and "Later" postpones
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
session** via `SteamClient.Apps.RemoveShortcut` (centralised in
`useGameActions.uninstall`, the choke point every surface goes through:
the Downloads row, the detail page's buttons, GameInfoCompatRow), so the
tile disappears immediately (library and Recents) with no restart; if you
were on the removed game's detail page, you are returned to the library.
Only if that live removal fails is the Steam restart prompt offered. One
important guard: the game folder is only deleted when it lives **inside
`~/Games/Manual`** — a folder the user manages themselves (a game added
already installed) is never touched. That protection has three layers:
(1) the uninstall RPC's generic *marker sweep* (which deletes any
directory carrying the `.unifideck_manifest.json` marker) is **skipped**
for the manual store — it once deleted an imported game's folder;
(2) the manifest is only written into plugin-created directories under
`~/Games/Manual`, never into user folders; and (3) on uninstall, any
stray marker an older build planted in a user folder is removed (the
bomb is defused) leaving everything else intact.

**IMPORT — adding an already-installed game**: the MANUAL INSTALL
section has two buttons. *Install* runs the flow above; *Import* adds a
game that is already installed: pick its executable, confirm the title,
and the record is born `ready` — then the game **launches once
automatically as a verification run** (`manual_import` emits the same
RunGame event): its prefix gets created right there and the user sees the
game actually works — later runs are instant. When that run ends, the
Steam restart is offered. Files stay where they are, and uninstalling
forgets
the game (shortcut, record, prefix) without deleting that user-managed
folder.

**The "Later" protection**: postponing the exe selection leaves the
record `installing`, and ANY later run of the game (Play re-launches the
installer) ends by re-offering the modal — the listener watches both the
auto-launch's exit and `game_stopped` events for manual games, with a
de-duplication guard. On the Downloads-tab row, a pending game's
**Play button opens the exe selector** instead of re-running the
installer — the pending step is the only thing between the user and
playing. And even on "Later", the shortcut already sits in
`shortcuts.vdf`, so the Steam restart is offered anyway so the tile
appears (the prompt skips itself when the tile is already live in the
session).

**Steam's flush and `manual_ensure_shortcut`**: every temp-shortcut
`AddShortcut` / `RemoveShortcut` makes Steam flush ITS in-memory copy of
`shortcuts.vdf` — which never contained our persistent row — erasing it
(the "tile never appears after restart" bug). So when each run ends (and
when the exe modal closes), the frontend waits out that flush (~2.5 s)
and calls `manual_ensure_shortcut`, which re-writes the row so it lands
AFTER the last flush and survives the restart. And when Play is pressed
in Downloads before restarting (the persistent shortcut is not
registered in the session yet), the launch goes through the
temp-shortcut path instead of a direct `RunGame` that would fail.

**Installing somewhere else**: the finalize confinement accepts the exe
in the U: folder, the prefix (C:), the installer's folder, and generally
any path under `$HOME` or `/run/media` (Wine's `Z:` drive lets the
wizard target any folder). This is safe because uninstall only deletes
directories inside `~/Games/Manual`. And when the game ended up INSIDE
the prefix (a C: install), uninstalling always removes the prefix —
keeping it would leak the "uninstalled" game on disk.

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
| `manual_import(exe_path, title)` | IMPORT button: a `ready` record straight from an already-installed game's exe |
| `manual_exe_candidates(game_id)` | Scans the U: folder and the prefix's `drive_c`, returns candidate `.exe`s for the modal |
| `manual_install_finalize(game_id, exe_path)` | Steps 16-20. Confines the exe to the game folder or its prefix (anti-traversal guard) |
| `manual_install_status(game_id)` | The current record (the frontend decides whether to ask for the exe after the app stops) |

The ad-hoc shortcut is written by `stores/manual/shortcut.py`
(`ensure_manual_game_shortcut`): it reads the vdf **from disk** (Steam
flushes over the in-memory copy), appends an entry with the same shape as
reconcile's `_build_shortcut_entry` (same appid, same LaunchOptions), and
the next sync's reconcile **adopts** it instead of duplicating it.

## 5. Drive U: — `stores/manual/drive.py` + launcher hook

`ensure_manual_drive(prefix_root, target_dir)` creates/repoints the
`<prefix>/dosdevices/u:` symlink. It never destroys a real directory
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
2. **"Later" on the exe modal** → safe: the game stays pending and
   pressing Play on its Downloads row re-opens the exe picker instead of
   launching anything.
3. **Installing onto C: instead of U:** works (the picker can navigate into
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
6. **Updating a game (patch executables)** — use the native context
   menu's **"Change executable…"** (enabled for manual games), which is
   confined to the game's folder: **copy the patch `.exe` into the game
   folder first**, pick it there, press Play (it runs inside the game's
   prefix with `U:` mapped), then pick the game's exe back and delete
   the patch file. A patch left outside the game folder is rejected by
   the override's install-dir confinement.

## 9. Verification

* `ruff`, `mypy`, `tsc`, `eslint`, `prettier`, rollup build, volumetry
  (files/functions/locals/nesting/fanout), `validate_event_schemas`,
  `check_config_keys` and the 4 i18n checks: **green**.
* `tests/unit/test_manual_store.py` (13 tests): state, library mapping,
  uninstall guards, id derivation, finalize confinement, shortcut
  creation/reuse, drive U: mapping, auth contract. Full suite: 2428 pass
  (the remaining battlenet-prefix/AuthDispatcher failures are pre-existing
  local-environment artifacts, present on the clean 0.7.4 base too).

Still to validate on real hardware: the first launch (prefix creation +
wizard opening), U: showing up inside the wizard, and the full cycle
through to playing.
