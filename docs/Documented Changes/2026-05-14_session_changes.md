# Session Changes — 2026-05-14

End-to-end repair of the `for-pr-0.7` branch after the F1-F8
frontend refactor. The new 6-layer architecture introduced a
chain of regressions: dead UI surfaces (App Details patch,
focus, tab switcher), broken RPC plumbing (envelope unwrap,
event names), backend wiring gaps (store DI never ran,
launcher couldn't see auth env vars), and CLI integration
bugs (Epic / Amazon `already authenticated` treated as failure).

This document is the post-mortem for every fix in the session,
grouped by subsystem and ordered roughly by user-facing impact.
Every fix is tied back to a concrete root cause; every code
change is justified against the PDF spec (v1.3 Definitive
Edition).

---

## Phase 1 — UI fundamentals lost in the refactor

### 1.1 App Details patch was dead in production

**Symptom:** non-Steam shortcuts (Epic / GOG / Amazon / Ubisoft /
Microsoft) never showed Unifideck's Play / Install row or the
Info panel; the App Details page rendered Steam's default UI
with no Unifideck overlay.

**Root cause:** [src/views/AppDetailsPatch.tsx](../../src/views/AppDetailsPatch.tsx)
was matching React tree nodes by `displayName.includes("PlaySection")` and
`displayName.includes("BasicAppDetails")`. Steam's production
React build mangles `displayName` to short strings; the matchers
silently returned `null` and `injectIntoTree` was a no-op.

**Fix:** Rewrote with anchor-based matching using stable CSS
class names imported from `@decky/ui`
(`appDetailsClasses.InnerContainer`, `playSectionClasses.Container`,
`appDetailsHeaderClasses.TopCapsule`). Switched from
`bridge.addRouterPatch(...)` to `afterPatch(routeProps, "renderFunc", ...)`

- `createReactTreePatcher`. Added the `__unifideckPatched` marker
  to prevent double-patching when ProtonDB / HLTB patch the same
  route. Added position-correction (if our injection drifts past
  index 3 after a Steam restart, splice it back). Restricted
  override to `appId > 2_000_000_000` (non-Steam shortcuts).

### 1.2 Gamepad focus was invisible everywhere

**Symptom:** on Steam Deck the L-stick / D-pad could skip
Unifideck's Play / Install / Cancel buttons; no focus halo was
ever drawn.

**Root cause:** the new presentational components used bare
`<div>` + `<DialogButton>`. Staging's `PlayButtonOverride` had
**11** `<Focusable>` wrappers and **5** `.gpfocus` CSS rules;
`GameInfoPanel` had **10** Focusable wrappers. None of that
survived the refactor.

**Fix:**

- Every play / info component now wraps its rows in
  `<Focusable flow-children="row" onActivate={() => {}}>`
  (per [MEMORY.md](../../../../.claude/projects/-home-deck-Documents-Projects-unifideck-main-unifideck-decky/memory/MEMORY.md):
  pass-through Focusable needs `onActivate`).
- New [src/components/play/play.css.ts](../../src/components/play/play.css.ts)
  injects the 5 staging `.gpfocus` rules (`.unifideck-install-btn.gpfocus`,
  `.unifideck-cancel-btn.gpfocus`, `.unifideck-play-btn.gpfocus`,
  `.unifideck-resume-btn.gpfocus`, `.unifideck-stop-btn.gpfocus`)
  into `<head>` once, called from `PlaySectionWrapper`'s mount.
- Touched: `NotInstalledButtons.tsx`, `DownloadingButtons.tsx`,
  `InstalledButtons.tsx`, `GameInfoPanel.tsx`, `GameInfoHeader.tsx`,
  `GameInfoMetadata.tsx`, `GameInfoScores.tsx`, `GameGrid.tsx`.

### 1.3 QAM tab switcher bypassed `@decky/ui`

**Symptom:** the Quick-Access tab buttons rendered as raw HTML
`<button>` with inline styles. Gamepad cursor couldn't land on
them; the default tab reset to "settings" on every QAM
dismount.

**Root cause:** [src/views/QuickAccessPanel.tsx](../../src/views/QuickAccessPanel.tsx)
used `<button onClick>` instead of `<DialogButton>`. Module-level
`persistentActiveTab` (which staging used to remember the last
tab) was missing.

**Fix:** Rewrote to use bare `<DialogButton>` in a flex row
(no outer `<Focusable>` — that swallows the focus target on
this Decky build), restored the module-level `persistentActiveTab`
so the last-viewed tab survives QAM open/close.

### 1.4 `StoragePathPicker.onConfirm` was missing

**Symptom:** clicking "Use this path" silently did nothing.

**Root cause:** [src/components/settings/StorageSettings.tsx](../../src/components/settings/StorageSettings.tsx)
mounted `<StoragePathPicker startPath={…} />` without the
required `onConfirm` callback (TypeScript caught it as a
type error but the build is rollup-warning-tolerant).

**Fix:** Wired `onConfirm` through. Created
[src/hooks/useStorageConfig.ts](../../src/hooks/useStorageConfig.ts)
exposing `setDefault` and `setCustomPath` actions so the
component stays presentational and the RPC traffic lives in
the hook (PDF rule).

### 1.5 Stale `useGameInfo` cache after uninstall

**Symptom:** uninstalling a game in App Details left the Play
row still showing "Play" instead of flipping to "Install".

**Root cause:** the module-level `useGameInfo` cache wasn't
invalidated on uninstall; the next render served the stale
"is_installed: true" entry.

**Fix:**

- [src/hooks/useGameInfo.ts](../../src/hooks/useGameInfo.ts) — exported
  `invalidateGameInfo(appId)` (handles signed/unsigned variants).
- [src/hooks/useGameActions.ts](../../src/hooks/useGameActions.ts) —
  `uninstall()` calls `invalidateGameInfo()` + `bumpGameStateVersion()`
  on success.
- Created [src/lib/game-state-version.ts](../../src/lib/game-state-version.ts)
  to break a circular dependency (`useGameActions → AppDetailsPatch
→ components/play → … → useGameActions`).

---

## Phase 2 — Missing modals & polish

### 2.1 Three dropped modals restored

The new architecture removed three modals without replacements
(legitimate regressions, not deliberate cuts).

- [src/components/modals/AuthSuccessModal.tsx](../../src/components/modals/AuthSuccessModal.tsx)
  — appears after `STORE_AUTH_COMPLETE`, navigates back to
  library home on dismiss.
- [src/components/modals/GOGLanguageSelectModal.tsx](../../src/components/modals/GOGLanguageSelectModal.tsx)
  — dropdown of GOG-supported languages, spawned from
  install flow when `get_gog_game_languages` returns > 1.
- [src/components/modals/ForceSyncModal.tsx](../../src/components/modals/ForceSyncModal.tsx)
  — confirmation before destructive force-sync (artwork-resync
  vs keep-artwork picker).

Also new:

- [src/components/modals/StorageBrowserModal.tsx](../../src/components/modals/StorageBrowserModal.tsx)
  — full-screen path picker wrapping `StoragePathPicker`.

### 2.2 New orchestration hook for GOG language

[src/hooks/useInstallFlow.tsx](../../src/hooks/useInstallFlow.tsx)
(new) — wraps `useGameActions.install` with the GOG language
side quest: fetches `get_gog_game_languages`, spawns
`<GOGLanguageSelectModal>` when more than one is offered.
Keeps `NotInstalledButtons.tsx` purely presentational per the
PDF rule.

### 2.3 Sync cooldown timer

[src/hooks/useSyncCooldown.ts](../../src/hooks/useSyncCooldown.ts)
(new) — 30 s manual-sync cooldown after each completed run.
Module-level state survives QAM dismount.

### 2.4 View-mode toggle on `GameInfoHeader`

[src/components/info/GameInfoHeader.tsx](../../src/components/info/GameInfoHeader.tsx)
— new `<DialogButton>` calling `useViewMode().toggle()` so
users can flip compact ↔ full layout.

### 2.5 Lazy cache priming

[src/hooks/useGameInfo.ts](../../src/hooks/useGameInfo.ts) —
stale-while-revalidate: paints any cached entry (even stale)
immediately, then refreshes in the background. Prevents the
blank-flash on first navigation.

---

## Phase 3 — Frontend RPC plumbing

### 3.1 EventBus poll spammed `records.filter is not a function`

**Symptom:** browser console showed ~30 errors/second from
`event-bus-client.ts:144`.

**Root cause:** the backend wraps every RPC return in
`{success, error, data}` via `@auto_wrap_rpc_methods`. `useRPC`
unwraps it for component callers, but
`EventBusClient.pollOnce()` called `@decky/api`'s `call()`
directly and tried `.filter(...)` on the envelope object.

**Fix:** Extracted `unwrapRpcEnvelope<T>(raw, options)` from
the existing logic in [src/api/useRPC.ts](../../src/api/useRPC.ts).
Used in:

- [src/api/event-bus-client.ts](../../src/api/event-bus-client.ts)
- [src/utils/authShortcutLaunch.ts](../../src/utils/authShortcutLaunch.ts)
- [src/utils/ubisoftShortcutLaunch.ts](../../src/utils/ubisoftShortcutLaunch.ts)
- [src/services/auth/AuthDispatcher.ts](../../src/services/auth/AuthDispatcher.ts)

One canonical envelope-unwrap function; rename → one-file
change.

### 3.2 DownloadsTab crash on click

**Symptom:** clicking the Downloads tab error-boundaried the
whole plugin (`Cannot read properties of undefined`).

**Root cause:** backend `get_download_queue` returns
`{queued, running}` but the frontend `DownloadQueueInfo` type
declares `{current, queued, finished, state}`. Reading
`queue.finished.length` on a missing field threw `TypeError`.

**Fix:**

- [src/contexts/DownloadContext.tsx](../../src/contexts/DownloadContext.tsx)
  — new `adaptQueue()` maps backend shape → frontend shape
  (`current = running[0]`, `finished = []`, `state` derived
  from `running.length`).
- [src/components/downloads/DownloadsTab.tsx](../../src/components/downloads/DownloadsTab.tsx)
  — defaults missing arrays to `[]` defensively.

### 3.3 Auth events silently ignored (Connect buttons never updated)

**Symptom:** Connect buttons stayed in "Connect" forever, even
after successful auth.

**Root cause:** [src/contexts/AuthContext.tsx](../../src/contexts/AuthContext.tsx)
subscribed to `Events.AUTH_COMPLETE` / `AUTH_FAILED` /
`LOGOUT_COMPLETE` — **none of which exist**. The bus emits
`STORE_AUTH_COMPLETE` / `STORE_AUTH_FAILED` / `STORE_LOGOUT`.
Status updates never fired.

**Fix:** Corrected the event names; subscription now matches
the bus contract.

### 3.4 `useGameInfo` called wrong RPC

**Symptom:** game metadata never loaded for non-Steam
shortcuts.

**Root cause:** `useGameInfo` called `rpcRoutes.getGameMetadata`
which has backend signature `get_game_metadata(store, game_id)`.
Frontend only knew the AppID at the call site.

**Fix:** Switched to `rpcRoutes.getGameInfo` (backend signature
`get_game_info(app_id: int)` — exactly the lookup the frontend
needs).

### 3.5 Injected components crashed with "called outside Provider"

**Symptom:** opening App Details for any non-Steam game
crashed the page with `useDownloads called outside <DownloadProvider>`.

**Root cause:** `AppDetailsPatch` splices `<PlaySectionWrapper>`
into Steam's own React tree, which renders **outside** our
top-level `<RootProvider>` (that only wraps `<QuickAccessPanel>`).
The injected components had no context.

**Fix:** New [src/contexts/InjectedSubtreeProvider.tsx](../../src/contexts/InjectedSubtreeProvider.tsx)
— same composition as `RootProvider` but without
`<ToastEventListener>` (the listener must stay singleton in
the QAM). `AppDetailsPatch` wraps every injection point in
this provider. Underlying singletons (`EventBusClient`,
`gameStateVersion`, module-level caches) keep both trees
coherent.

### 3.6 Frontend rpcRoutes registry bypass

**Symptom:** PDF rule "raw string method names never appear
elsewhere" violated by the shortcut launchers — they passed
literal strings like `"get_epic_auth_shortcut_context"` to
`call()`.

**Fix:** Added 6 new entries to
[src/api/rpc-routes.ts](../../src/api/rpc-routes.ts):
`getEpicAuthShortcutContext`, `getGogAuthShortcutContext`,
`getAmazonAuthShortcutContext`, `getMicrosoftAuthShortcutContext`,
`getUbisoftAuthShortcutContext`, `getCompatToolForGame`. Updated
`AuthShortcutConfig.contextRpcMethod` to type
`RouteName`. All raw-string `call()` sites now reference
`rpcRoutes.*`.

### 3.7 `useStoreAuth.connect()` orchestration moved to `AuthDispatcher`

**Symptom:** the hook owned multi-stage logic (toast → backend
prep → shortcut launch → result toast). PDF spec keeps
multi-stage orchestration in `services/auth/AuthDispatcher.ts`.

**Fix:** [src/services/auth/AuthDispatcher.ts](../../src/services/auth/AuthDispatcher.ts)
now owns the entire handshake: backend prep via
`rpcRoutes.storeAuth`, shortcut launch via
`launch<Store>AuthViaShortcut()`, event subscription for
`STORE_AUTH_COMPLETE` / `_FAILED`, per-store mutex, 10-minute
timeout. [src/hooks/useStoreAuth.ts](../../src/hooks/useStoreAuth.ts)
shrank to a thin React adapter.

---

## Phase 4 — Backend RPC mixins

### 4.1 Mixins consolidated to stay under 200-LOC ceiling

PDF rule: "no monolith above 200 LOC". My additions pushed
`store.py` and `download.py` over budget. Split into focused
mixins:

- [py_modules/unifideck/rpc/mixins/storage.py](../../py_modules/unifideck/rpc/mixins/storage.py)
  (new, 165 LOC) — `get_storage_locations`,
  `set_default_storage_location`, `set_custom_install_path`
  extracted from `download.py`.
- [py_modules/unifideck/rpc/mixins/auth_shortcuts.py](../../py_modules/unifideck/rpc/mixins/auth_shortcuts.py)
  (new, 176 LOC) — `get_<store>_auth_shortcut_context` and
  `get_compat_tool_for_game` extracted from `store.py`.

Wired into Plugin class bases in [main.py](../../main.py):

```python
class Plugin(
    ObservabilityRPCMixin, SecurityRPCMixin,
    DownloadRPCMixin, StorageRPCMixin,           # ← split
    LaunchRPCMixin,
    StoreRPCMixin, AuthShortcutsRPCMixin,         # ← split
    SyncRPCMixin, UIRPCMixin,
    CloudFailureRPCMixin, ConfigValidationRPCMixin,
    PlaytimeRPCMixin, ActionRPCMixin,
):
```

Post-split sizes: `download.py = 174`, `store.py = 77`,
`storage.py = 165`, `auth_shortcuts.py = 176`, `ui.py = 184`
— all under 200.

### 4.2 New RPCs for auth shortcut launchers

The frontend launchers in
[utils/authShortcutLaunch.ts](../../src/utils/authShortcutLaunch.ts)
need shortcut metadata (appid + launcher path) to find or
create the Steam shortcut. Backend exposed these via
`AuthShortcutsRPCMixin`:

- `get_epic_auth_shortcut_context`
- `get_gog_auth_shortcut_context`
- `get_amazon_auth_shortcut_context`
- `get_microsoft_auth_shortcut_context`
- `get_ubisoft_auth_shortcut_context` (proxies to
  `UbisoftStore.get_auth_shortcut_context` — has VDF-scan +
  repair logic)
- `get_compat_tool_for_game` (wraps existing
  `compatibility.proton_helpers` helper for compat-tool save
  / restore around auth)

Returned shape matches frontend `AuthShortcutContextRPC`:
`{success, appid_unsigned, launcher_path, launch_options,
launch_wait_ms}`. The `appid_unsigned` mirrors what
`ShortcutService.add_auth_shortcut` writes to `shortcuts.vdf`
(deterministic via `generate_app_id`). `launcher_path`
returns `bin/unifideck-launcher` so the frontend's temp-shortcut
fallback uses the actual executable wrapper.

### 4.3 Storage RPCs

- `get_storage_locations` reshaped from
  `[{path, free_bytes, total_bytes}]` to
  `{success, locations: [{id, label, path, available,
free_space_gb}], default}` matching frontend
  `StorageLocationsResponse`.
- `set_default_storage_location(loc_id)` — persists
  `download.default_location`.
- `set_custom_install_path(path)` — validates path exists +
  writable, persists `download.custom_path`.
- `list_directory(path, show_hidden, sort_by)` — `os.scandir`-
  based directory enumeration for `StoragePathPicker` (handles
  `PermissionError` / `OSError` as structured non-success
  responses).

### 4.4 `notify_game_launched` / `notify_game_stopped` signature mismatch

**Symptom:** every Steam app start/stop logged an `RpcError:
missing 1 required positional argument: 'game_id'`.

**Root cause:** backend required `(store, game_id)` but the
frontend bootstrap subscriber only had Steam's `unAppID`:

```ts
void call(rpcRoutes.notifyGameLaunched, n.unAppID).catch(() => {});
```

**Fix:** [py_modules/unifideck/rpc/mixins/launch.py](../../py_modules/unifideck/rpc/mixins/launch.py)
now accepts both signatures:

1. `notify_game_launched(app_id)` → resolves `store` / `game_id`
   via `sync_service.get_game_info(app_id)`; quietly skips
   (`{success: true, skipped: "not_unifideck_app"}`) if the
   AppID isn't a Unifideck shortcut.
2. `notify_game_launched(store=…, game_id=…)` — explicit form
   still works.

Shared helper `_resolve_app_id()` smoke-tested for all 4 cases.

### 4.5 `get_game_info` awaited a non-coroutine

**Symptom:** `TypeError: object NoneType can't be used in
'await' expression`.

**Root cause:** `SyncService.get_game_info` is **synchronous**
(linear scan over `_all_games`); mixin had a stray `await`.

**Fix:** Dropped `await` in
[py_modules/unifideck/rpc/mixins/sync.py](../../py_modules/unifideck/rpc/mixins/sync.py).

### 4.6 `store_auth` and `get_<store>_auth_shortcut_context` had no logging

**Symptom:** silent backend with no way to correlate frontend
clicks to backend execution.

**Fix:** Added INFO-level logging on entry + exit:

- `[StoreAuth:<store>] action=start kw={…}` /
  `success=<bool> error=<str>`
- `[AuthShortcuts:<store>] context requested` /
  `context resolved: appid=<int> launcher=<path>`
- `[AuthShortcuts] get_compat_tool_for_game(<key>)` /
  `compat_tool result: success=<bool>`

The `code` kwarg (2FA codes) is stripped from logged kwargs.

---

## Phase 5 — Backend launcher dispatcher

### 5.1 Dispatcher tried games.map lookup for auth shortcuts

**Symptom:** clicking Connect opened the auth shortcut, the
launcher ran, then immediately crashed with
`GameNotFoundError("game 'amazon:amazon-auth' not found in
games.map")`.

**Root cause:** [py_modules/unifideck/launcher/dispatcher.py](../../py_modules/unifideck/launcher/dispatcher.py)
called `shortcut_svc.get_entry_for_game_key(store, game_id)`
**before** `_detect_auth_action()`. Auth shortcuts aren't in
games.map (they're not games), so the lookup raised before
the auth-action code path ran.

**Fix:** `_build_context` now calls `_detect_auth_action()`
FIRST. When auth is detected, builds a minimal
`LaunchContext(is_launch_action=False, auth_store=…,
exe_path=/dev/null, work_dir=plugin_dir)` and skips the
games.map lookup. `LauncherService.launch()` already
branches to `handle_store_auth(ctx, edge_browser)` when
`is_launch_action=False`.

Also added `"microsoft"` and `"ubisoft"` env-var keys to
`_detect_auth_action` (only `epic`/`gog`/`amazon` were
checked).

### 5.2 Launch options not propagated to env

**Symptom:** even after fix 5.1, `_detect_auth_action()`
returned `(None, True)` (no auth) — but the auth shortcut
WAS the one being launched.

**Root cause:** Steam passes plugin launch options as **argv**,
not as environment variables. The shortcut's launch options
`"amazon:amazon-auth UNIFIDECK_AMAZON_ACTION=auth"` arrived as
`sys.argv[1:] = ["amazon:amazon-auth", "UNIFIDECK_AMAZON_ACTION=auth"]`.
`_detect_auth_action` read `os.environ.get(...)` — the env
var was never set, returns `None`.

**Fix:** New `_promote_env_tokens(raw_options)` helper —
parses `KEY=value` tokens from the joined argv tail, promotes
any `KEY` starting with `UNIFIDECK_` to `os.environ` (uses
`setdefault` so a real env var wins if Steam ever evolves to
pass them properly). Called from `_build_context` BEFORE
`_detect_auth_action()`. Smoke-tested for Amazon, Microsoft,
and non-UNIFIDECK key rejection.

### 5.3 `inject_store_dependencies` never called

**Symptom:** `[StoreAuth:<store>] action=start success=False
error=auth_not_configured` for every store.

**Root cause:** PDF [OP-13g](../../py_modules/unifideck/services/bootstrap/store_injector.py)
defines `inject_store_dependencies()` and exports it from
`services/bootstrap/__init__.py`, but **nothing in the boot
sequence called it**. So `container.browser_monitor` and
`container.shortcut` were built fine, but never wired into
the auto-discovered store instances.

**Fix:**

- [py_modules/unifideck/bootstrap/boot.py](../../py_modules/unifideck/bootstrap/boot.py)
  — `_boot_layer5_services` now does 3-phase wiring:
  ```
  bootstrap_services(...)              # build container
  inject_store_dependencies(...)       # NEW — wire stores
  start_async_services(...)            # kick workers
  ```
- [py_modules/unifideck/services/bootstrap/container.py](../../py_modules/unifideck/services/bootstrap/container.py)
  — added `browser_monitor` field to `ServiceContainer`.
- [py_modules/unifideck/services/bootstrap/constructor.py](../../py_modules/unifideck/services/bootstrap/constructor.py)
  — after the service-defs loop, instantiates
  `OAuthBrowserMonitor(cdp_client=container.cdp, config=config)`
  and stores it on `container.browser_monitor`.
- [py_modules/unifideck/services/bootstrap/store_injector.py](../../py_modules/unifideck/services/bootstrap/store_injector.py)
  — extended `_STORE_INJECTIONS` from `microsoft`-only to all
  5 stores; each gets `_browser_monitor` + `_shortcut_service`
  (Ubisoft only needs `_shortcut_service`).
- Added a `_rebuild_auth_after_injection` hook the injector
  calls after setting attributes, so stores can construct
  their auth flow against the freshly-injected
  `_browser_monitor`.

### 5.4 Stores constructed `_auth` at `__init__` (before container existed)

**Symptom:** even after the injector wired
`_browser_monitor`, stores still reported
`auth_not_configured`.

**Root cause:** Each store's `__init__` had a guard:

```python
if browser_monitor is not None:
    self._auth = SomeAuthFlow(...)
else:
    self._auth = None
```

`StoreRegistry.auto_discover()` constructs stores with
`browser_monitor=None` (the container doesn't exist yet), so
`_auth` was permanently `None`. Setting `_browser_monitor`
post-hoc didn't rebuild `_auth`.

**Fix:** Each store now lazily builds `_auth` in a
`_rebuild_auth_after_injection()` method called by the
injector:

- [py_modules/unifideck/stores/amazon/amazon_store.py](../../py_modules/unifideck/stores/amazon/amazon_store.py)
- [py_modules/unifideck/stores/epic/store.py](../../py_modules/unifideck/stores/epic/store.py)
- [py_modules/unifideck/stores/gog/store.py](../../py_modules/unifideck/stores/gog/store.py)
- [py_modules/unifideck/stores/microsoft/microsoft_store.py](../../py_modules/unifideck/stores/microsoft/microsoft_store.py)

GOG additionally rebuilds its `_installer` / `_dlc` /
`_updates` submodules (they reference the token manager that
`_auth` may refresh). All four hooks are idempotent
(early-return if `_auth` is already wired). Smoke-tested for
Amazon: `_auth=None` before injection, `AmazonAuthFlow` after,
idempotent on second call.

### 5.5 Binary resolver rejected relative search paths

**Symptom:** backend log showed
`[BinaryResolver] nile not found in any tier` and `legendary
not found in any tier` even though the CLIs were installed at
`/home/deck/homebrew/plugins/Unifideck/bin/`.

**Root cause:** Epic and Amazon declared
`search_paths=["bin/legendary"]` and `["bin/nile"]`
(relative). [BinaryResolver](../../py_modules/unifideck/core/binaries/binary_resolver.py)'s
Tier-1 lookup explicitly checks `Path(expanded).is_absolute()`
— relative paths were rejected, Tier-2 (`shutil.which`) and
Tier-3 (`~/.local/bin`) had nothing → "not found in any tier".

**Fix:** [py_modules/unifideck/stores/shared/store_base.py](../../py_modules/unifideck/stores/shared/store_base.py)
— `_find_binary()` now absolutises relative search_paths
against `self._plugin_dir` before delegating to the resolver.
GOG already worked because it has its own
`_resolve_gogdl_bin()` that joins manually; Epic and Amazon
benefit immediately.

### 5.6 `logger` undefined when `vdf` import succeeded

**Symptom:** clicking Connect for any store crashed
`start_auth` with `NameError: name 'logger' is not defined`.

**Root cause:** [py_modules/unifideck/steam/shortcuts.py:40](../../py_modules/unifideck/steam/shortcuts.py#L40)
defined `logger = logging.getLogger(__name__)` **only inside
the `except ImportError` branch**. `vdf` always imports
successfully on a real install, so `logger` was never created
— but the rest of the module called `logger.info(...)` at
runtime.

**Fix:** Moved `logger = logging.getLogger(__name__)` out of
the try/except to module scope. Smoke-tested:
`logger.name == 'unifideck.steam.shortcuts'`.

---

## Phase 6 — CLI integration & fast-path UX

### 6.-1 `AuthDispatcher.runFlow` hung at "Working…" on fast-path success

**Symptom:** after the 6.0 fix below skipped the shortcut
launch, clicking Connect on an already-authed store left the
button stuck on "Working…" forever.

**Root cause:** [src/services/auth/AuthDispatcher.ts](../../src/services/auth/AuthDispatcher.ts)'s
`runFlow` Promise resolves only via the EventBus subscriptions
to `STORE_AUTH_COMPLETE` / `_FAILED`. The fast-path
short-circuited `kickAndLaunch` and returned without
launching the shortcut — but the backend's
`STORE_AUTH_COMPLETE` event raced the RPC response: the
EventBus polls every 250-2000 ms, so the event was either
delivered before the subscription was active OR was missed
by `lastSeenTimestamp` dedup. The Promise hung until the
10-minute timeout.

**Fix:** `kickAndLaunch` now returns
`Promise<AuthResult | null>`. On fast-path success it
returns `{success: true, store}`; on slow-path it returns
`null` and the caller waits for the event. `runFlow` checks
the return value:

```ts
void this.kickAndLaunch(store)
  .then((early) => {
    if (early) onResolved(early); // ← resolves immediately
  })
  .catch(reject);
```

Both paths resolve cleanly — no race, no timeout.

### 6.0 `AuthDispatcher` launched shortcut even after fast-path success

**Symptom:** after the Phase 6.1 backend fix, the log
showed `[epic_auth] credentials still valid — skipping OAuth`
and `[StoreAuth:epic] action=start success=True` — but the
launcher still ran moments later, the `unifideck-launcher`
binary hit a `DependencyMissingError("Microsoft Edge flatpak
required for OAuth")` inside `handle_store_auth`, and the
user saw "launcher crashes immediately".

**Root cause:** [src/services/auth/AuthDispatcher.ts](../../src/services/auth/AuthDispatcher.ts)'s
`kickAndLaunch` had two unconditional steps: call
`rpcRoutes.storeAuth`, then call `launchForStore(store)`. The
fast-path success was already emitted as
`STORE_AUTH_COMPLETE` by the backend (resolving the
surrounding Promise via the event subscription), but the
shortcut launch fired anyway.

**Fix:** `kickAndLaunch` now inspects the unwrapped
`store_auth` response. When `startResult.success === true`,
it returns early — skipping the shortcut launch entirely.
For already-authed stores this means: backend emits
`STORE_AUTH_COMPLETE` → frontend's `runFlow` Promise
resolves via the subscription → UI flips to "Connected"
without ever running the launcher binary.

### 6.1 Epic / Amazon "already logged in" treated as error

**Symptom:** Connect Epic / Amazon failed with
`get_url_failed`. Manual probe of the CLIs revealed:

```
$ legendary auth
[cli] INFO: Stored credentials are still valid

$ nile auth --login --non-interactive
ERROR [CLI]: You are already logged in
```

The auth orchestrator expected to scrape a NEW OAuth URL —
when no URL appeared, it raised `StoreAuthError`. But the user
was already authenticated; the correct behaviour is to
report success without opening a browser.

**Fix:**

- [py_modules/unifideck/stores/epic/auth.py](../../py_modules/unifideck/stores/epic/auth.py)
  — `EpicAuthFlow.start_auth` now calls
  `_is_already_authed()` first. The check runs `legendary auth`
  (no args), reads stdout, looks for
  `_LEGENDARY_ALREADY_AUTHED_MARKERS` (`"Stored credentials
are still valid"`, `"Login successful"`). On hit, emits
  `STORE_AUTH_COMPLETE` and returns `AuthResult(success=True)`.
- [py_modules/unifideck/stores/amazon/amazon_auth.py](../../py_modules/unifideck/stores/amazon/amazon_auth.py)
  — same pattern via `_is_already_authed()`. Runs
  `nile auth --login --non-interactive`, checks stderr/stdout
  for `_NILE_ALREADY_AUTHED_MARKERS` (`"You are already
logged in"`, `"already authenticated"`). On hit, emits
  `STORE_AUTH_COMPLETE` + success.

---

## Phase 7 — Microsoft Edge install prerequisite

### 7.0 Edge install flow never reached the UI

**Symptom:** opening Connect on any OAuth store (Epic / GOG /
Amazon / Microsoft) with Edge not installed silently failed.
Either the auth flow returned `edge_not_installed` as a raw
error string (GOG / Microsoft) or the launcher subprocess
crashed mid-flight (Epic / Amazon). No modal, no install
button.

**Root cause:** the PDF spec ([OP-29e](../../py_modules/unifideck/auth/edge_browser/installer.py),
[OP-53](../../py_modules/unifideck/stores/microsoft/microsoft_store.py))
defined `EdgeInstaller` + `MicrosoftStore.install_edge` but
**never** exposed them as RPCs and **never** specified a
frontend modal. Additionally :

1. `EdgeBrowser` was never instantiated in the running plugin
   — `auto_discover` constructed stores without an
   `edge_browser=` argument, so even GOG/Microsoft had
   `self._edge = None` permanently.
2. Epic and Amazon stores had no Edge check at all — they
   let the launcher subprocess crash with
   `DependencyMissingError` instead of returning a
   structured error.
3. The frontend had no modal — the staging-era
   `ChromiumInstallModal` was deleted in the F1-F8 refactor
   and not restored.

**Fix (5 pieces, fully covers Epic / GOG / Amazon / Microsoft
— Ubisoft excluded since it uses its own client):**

1. **Backend container wiring**
   ([py_modules/unifideck/services/bootstrap/container.py](../../py_modules/unifideck/services/bootstrap/container.py),
   [constructor.py](../../py_modules/unifideck/services/bootstrap/constructor.py))
   — added `edge_browser: EdgeBrowser | None` field to
   `ServiceContainer`. After the service-defs loop,
   `bootstrap_services` instantiates a single shared
   `EdgeBrowser(cdp_port=config["edge.cdp_port"], locale_fn=…)`
   and stores it on the container.

2. **Store injection**
   ([store_injector.py](../../py_modules/unifideck/services/bootstrap/store_injector.py))
   — extended `_STORE_INJECTIONS` so each of `amazon`, `epic`,
   `gog`, `microsoft` gets `("_edge", "edge_browser")` set
   post-construction. Ubisoft skipped.

3. **Edge prereq check on all 4 OAuth stores** — Epic
   ([epic/store.py:211](../../py_modules/unifideck/stores/epic/store.py))
   and Amazon
   ([amazon/amazon_store.py:174](../../py_modules/unifideck/stores/amazon/amazon_store.py))
   now mirror GOG / Microsoft : before kicking the auth flow,
   check `self._edge is None or not self._edge.is_installed`
   and return `AuthResult(success=False, error="edge_not_installed")`.
   The launcher subprocess never runs without Edge present —
   no more `DependencyMissingError` crashes.

4. **New `EdgeRPCMixin`**
   ([py_modules/unifideck/rpc/mixins/edge.py](../../py_modules/unifideck/rpc/mixins/edge.py),
   ~100 LOC) — exposes two RPCs:

   - `is_edge_installed()` → `{installed: bool}` — pre-flight
     check, proxies through `MicrosoftStore.is_edge_installed`.
   - `install_edge()` → `{success: bool, error?: str}` —
     triggers the flatpak install via
     `MicrosoftStore.install_edge` → `EdgeBrowser.install` →
     `EdgeInstaller._run_flatpak_install`. Both calls log
     entry + result for diagnostics. Wired into the Plugin
     class in [main.py](../../main.py) right after
     `AuthShortcutsRPCMixin`.

5. **Frontend modal**
   ([src/components/modals/ChromiumInstallModal.tsx](../../src/components/modals/ChromiumInstallModal.tsx),
   ~95 LOC, restored from staging) — `ConfirmModal` with
   localized title / description / "Install" button. Calls
   `rpcRoutes.installEdge` via the standard envelope-unwrap
   helper, shows a `<Spinner>` while the flatpak install
   runs (30-90 s typical), toasts the result, calls
   `onInstalled()` on success. Barrel
   ([modals/index.ts](../../src/components/modals/index.ts))
   updated.

6. **Trigger + auto-retry**
   ([src/hooks/useStoreAuth.tsx](../../src/hooks/useStoreAuth.tsx),
   renamed from `.ts` because it now embeds JSX) —
   `connect()` inspects the `AuthDispatcher` result. When
   `result.error === "edge_not_installed"`, spawns
   `<ChromiumInstallModal>` with an `onInstalled` callback
   that re-invokes `connect()` so the user doesn't have to
   click Connect a second time.

**Resulting flow** when Edge is missing:

```
Click Connect → AuthDispatcher.start(store)
  → store.start_auth() → returns edge_not_installed
  → frontend sees error → showModal(<ChromiumInstallModal/>)
  → user clicks "Install Microsoft Edge"
  → backend `install_edge` → flatpak install com.microsoft.Edge
  → success toast → modal closes → onInstalled() fires
  → connect() retries → real auth flow runs
```

**i18n** — all `microsoft.chromium*` and
`microsoft.browser*` keys already present in
`en-US.json` from the staging-era catalog merge.

---

## How the EventBus, store_injector, and RPCs fit together

Three orthogonal mechanisms, all per PDF spec:

| Concern                                       | Mechanism                                                                   | When                                         |
| --------------------------------------------- | --------------------------------------------------------------------------- | -------------------------------------------- |
| **Build the service graph at boot**           | `bootstrap_services()` (OP-13d)                                             | once, at plugin mount                        |
| **Wire services into auto-discovered stores** | `inject_store_dependencies()` (OP-13g)                                      | once, immediately after `bootstrap_services` |
| **Trigger an action with a return value**     | RPC: `store_auth`, `install_game`, etc.                                     | per user click                               |
| **Fan out a state change**                    | EventBus: `STORE_AUTH_COMPLETE`, `SYNC_PROGRESS`, `DOWNLOAD_PROGRESS`, etc. | continuous, multiple subscribers             |

The EventBus is _not_ used for service construction or for
action triggering — it is the broadcast channel for runtime
state changes. The frontend's `AuthDispatcher` calls
`store_auth` (RPC) to _start_ a flow, then awaits
`STORE_AUTH_COMPLETE` / `_FAILED` on the EventBus to _complete_
it.

---

## Files touched (full list)

### Frontend — new files

- `src/components/play/play.css.ts`
- `src/components/modals/AuthSuccessModal.tsx`
- `src/components/modals/GOGLanguageSelectModal.tsx`
- `src/components/modals/ForceSyncModal.tsx`
- `src/components/modals/StorageBrowserModal.tsx`
- `src/contexts/InjectedSubtreeProvider.tsx`
- `src/hooks/useStorageConfig.ts`
- `src/hooks/useInstallFlow.tsx`
- `src/hooks/useSyncCooldown.ts`
- `src/lib/game-state-version.ts`

### Frontend — modified

- `src/views/AppDetailsPatch.tsx` (anchor-based patching +
  `InjectedSubtreeProvider` wrap)
- `src/views/QuickAccessPanel.tsx` (Decky-native tabs +
  `persistentActiveTab`)
- `src/components/play/{Play,Not,Down,In}*.tsx` (Focusable +
  gpfocus)
- `src/components/info/*.tsx` (Focusable)
- `src/components/shared/GameGrid.tsx` (Focusable + DialogButton)
- `src/components/settings/StorageSettings.tsx` (browser modal)
- `src/components/settings/StoragePathPicker.tsx` (degraded
  on missing RPC)
- `src/components/settings/LibrarySync.tsx` (cooldown +
  ForceSyncModal)
- `src/components/info/GameInfoHeader.tsx` (view-mode toggle)
- `src/components/downloads/DownloadsTab.tsx` (defensive
  defaults)
- `src/components/modals/ToastEventListener.tsx`
  (`STORE_AUTH_COMPLETE` → `AuthSuccessModal`)
- `src/components/modals/index.ts` (barrel)
- `src/contexts/index.ts` (barrel)
- `src/contexts/AuthContext.tsx` (corrected event names)
- `src/contexts/DownloadContext.tsx` (adapter +
  `language` install option)
- `src/contexts/SyncContext.tsx` (`forceSync(resyncArtwork?)`)
- `src/hooks/useGameInfo.ts` (`invalidateGameInfo` + lazy
  priming + route fix)
- `src/hooks/useGameActions.ts` (cache invalidation)
- `src/hooks/useStoreAuth.ts` (delegates to `AuthDispatcher`)
- `src/hooks/index.ts` (barrel)
- `src/services/auth/AuthDispatcher.ts` (full orchestration)
- `src/api/useRPC.ts` (`unwrapRpcEnvelope` export)
- `src/api/event-bus-client.ts` (envelope unwrap)
- `src/api/rpc-routes.ts` (6 new routes)
- `src/utils/authShortcutLaunch.ts` (envelope unwrap +
  rpcRoutes)
- `src/utils/ubisoftShortcutLaunch.ts` (envelope unwrap +
  rpcRoutes)
- `src/i18n/locales/en-US.json` (5 new keys: `sync.cooldown`,
  `sync.artworkPhase`, `info.expandDetails`,
  `info.collapseDetails`, `common.ok`)

### Backend — new files

- `py_modules/unifideck/rpc/mixins/storage.py`
- `py_modules/unifideck/rpc/mixins/auth_shortcuts.py`

### Backend — modified

- `main.py` (added 2 mixins to Plugin bases)
- `py_modules/unifideck/bootstrap/boot.py`
  (`inject_store_dependencies` call)
- `py_modules/unifideck/services/bootstrap/container.py`
  (`browser_monitor` field)
- `py_modules/unifideck/services/bootstrap/constructor.py`
  (post-loop `OAuthBrowserMonitor` construction)
- `py_modules/unifideck/services/bootstrap/store_injector.py`
  (4 new injection table entries +
  `_rebuild_auth_after_injection` hook)
- `py_modules/unifideck/rpc/mixins/store.py` (logging on
  `store_auth`; auth-shortcut RPCs moved out)
- `py_modules/unifideck/rpc/mixins/download.py` (storage RPCs
  moved out)
- `py_modules/unifideck/rpc/mixins/ui.py` (added
  `list_directory`)
- `py_modules/unifideck/rpc/mixins/launch.py`
  (`notify_game_launched/stopped` accept `app_id`)
- `py_modules/unifideck/rpc/mixins/sync.py` (dropped stray
  `await`)
- `py_modules/unifideck/stores/shared/store_base.py`
  (`_find_binary` absolutises relative paths)
- `py_modules/unifideck/stores/amazon/amazon_store.py`
  (lazy auth build)
- `py_modules/unifideck/stores/amazon/amazon_auth.py`
  (already-authed fast path)
- `py_modules/unifideck/stores/epic/store.py` (lazy auth
  build)
- `py_modules/unifideck/stores/epic/auth.py` (already-authed
  fast path)
- `py_modules/unifideck/stores/gog/store.py` (lazy auth
  build + submodule rebuild)
- `py_modules/unifideck/stores/microsoft/microsoft_store.py`
  (lazy auth build)
- `py_modules/unifideck/launcher/dispatcher.py` (auth-shortcut
  short-circuit + env-token promotion + Ubisoft/Microsoft
  detection)
- `py_modules/unifideck/steam/shortcuts.py`
  (module-scope `logger`)

---

## Verification status

- `npm run build` clean (pre-existing warnings only:
  `ControllerInfo` unused, `router-patch.ts` TS2322).
- Python: every touched file passes `ast.parse`.
- Live verified at the deck:
  - Plugin boots end-to-end ✓
  - All 5 stores `registered` + `auth rebuilt after injection` ✓
  - `OAuthBrowserMonitor` wired into the container ✓
  - Connect Epic invokes `legendary auth`, detects "Stored
    credentials are still valid" (after this commit's fix) ✓
  - Connect Amazon invokes `nile auth --login --non-interactive`,
    detects "You are already logged in" (after this commit's
    fix) ✓
  - Launcher dispatcher: `auth shortcut detected: auth_store=epic`
    log line proves the dispatcher path works end-to-end ✓
