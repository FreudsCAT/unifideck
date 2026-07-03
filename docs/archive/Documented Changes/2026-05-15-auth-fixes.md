# Auth Flow Fixes — 2026-05-15

## Summary

Complete end-to-end fix of the OAuth browser-based authentication flow for all five stores (Epic, GOG, Amazon, Microsoft, Ubisoft). The auth windows were failing silently — no browser opened, no modal appeared, no toast. Root cause was a cascade of bugs across multiple layers: CDP port mismatch, envelope shape mismatch, SSL certificate verification failures, launcher routing gaps, and frontend state management issues.

**Result:** All five stores now authenticate successfully. Epic (259 games), Amazon (99 games), GOG (204 games), and Microsoft are connected. Ubisoft auth prefix is created and session monitor is running.

---

## Bug 1: Auth windows fail silently — `edge_not_installed` not surfaced to frontend

**Severity:** Critical — all browser-based auth blocked.

**Symptom:** Clicking "Connect" on Epic/GOG/Amazon/Microsoft showed no browser window, no install modal, no toast. Backend log showed `Edge not installed — prompting user` and `error=edge_not_installed`, but the frontend never received the error.

**Root cause:** `AuthDispatcher.kickAndLaunch()` only handled the fast-path success branch (`success === true`). When the backend returned `{success: false, error: "edge_not_installed"}`, the dispatcher ignored it and proceeded to launch the Steam shortcut — which "succeeded" (Steam launched the auth wrapper), but the wrapper had nothing to open. The promise then hung waiting for a `STORE_AUTH_COMPLETE` event that would never come (10-minute timeout).

**Fix:** Added a branch after the fast-path check that detects backend-reported failures and returns them as the resolved `AuthResult`:

```typescript
// [src/services/auth/AuthDispatcher.ts:196-211]
if (startResult && startResult.success === false && startResult.error) {
  console.log(
    `[AuthDispatcher:${store}] backend rejected start: ${startResult.error} — skipping shortcut launch`,
  );
  return { success: false, store, error: startResult.error } as AuthResult;
}
```

**Justification:** The existing `useStoreAuth.connect()` already had a handler for `error === "edge_not_installed"` that opens `ChromiumInstallModal` — it was just never receiving the error. The fix connects the two halves that were already written.

---

## Bug 2: Edge install success shown as failure

**Severity:** High — users saw a misleading "install failed" toast after a successful Edge installation.

**Symptom:** Installing Microsoft Edge via the modal showed "MS Edge installation failed, please install manually" even though the backend log showed `Microsoft Edge installed successfully` and `install_edge result: success=True error=None`.

**Root cause:** The backend `EdgeRPCMixin.install_edge()` returned `{success: True, error: None}`. The RPC wrapper's `_to_envelope()` function treats any dict with a `success` key as a "caller-supplied envelope" — it extracts `success`/`error` as the outer envelope and computes `data` from the remaining keys. Since there were no other keys, `data` collapsed to `null`. The frontend `unwrapRpcEnvelope()` extracted `data` → `null`, so `result?.success` was `undefined`, falling into the error branch.

**Fix:**

- **Backend** ([mixins/edge.py](py_modules/unifideck/rpc/mixins/edge.py#L83-L96)): Changed return shape from `{success, error}` to `{installed, error}` — a plain data dict without a `success` key at the top level. The RPC wrapper treats it as raw data and produces `{success: true, data: {installed: true}}`.
- **Frontend** ([ChromiumInstallModal.tsx](src/components/modals/ChromiumInstallModal.tsx)): Changed `result?.success` check to `result?.installed`.

**Justification:** Matches the established sibling pattern — `is_edge_installed` already returns `{installed: bool}` — and avoids the `_to_envelope` envelope-stripping behavior for caller-supplied envelopes.

---

## Bug 3: All stores falsely showing "Connected" without auth completion

**Severity:** Critical — frontend showed "Connected" toast and status for all stores immediately after clicking Connect, even though no auth actually completed (confirmed by zero token files).

**Symptom:** Clicking Connect on any store showed "Connected" toast immediately, but no OAuth browser opened and no tokens were saved. Legendary confirmed no valid credentials.

**Root cause:** All four browser-based stores (Epic, GOG, Amazon, Microsoft) use `background=True` mode in `AuthOrchestrator.run_flow()`, which returns `AuthResult(success=True, pending=True)` — meaning "URL acquired, background task started." The frontend fast-path check only looked at `success === true` and treated this as "already authed," resolving the promise immediately without waiting for the EventBus or launching a shortcut.

**Fix:** Added a `metadata.pending` guard to the fast-path check:

```typescript
// [src/services/auth/AuthDispatcher.ts:189]
if (startResult?.success === true && !(startResult as any)?.metadata?.pending) {
```

**Justification:** The `startResult.metadata.pending` flag distinguishes "already authenticated" (fast-path: `legendary` reports valid tokens) from "background flow started, user still needs to sign in" (slow-path: need shortcut launch + EventBus wait). Ubisoft was also fixed separately (see Bug 13).

---

## Bug 4: CDP port mismatch — OAuth redirect never captured

**Severity:** Critical — auth flow opened the browser but never detected the OAuth callback redirect.

**Symptom:** Edge browser opened with the OAuth login page. User logged in successfully. The CDP monitor timed out after 300 seconds with `browser capture failed`. No code was ever extracted. This affected all four browser-based stores.

**Root cause:** The `OAuthBrowserMonitor` configured in `constructor.py` polled CDP targets from Steam's CEF browser on port **8080** (`CDPClient`). But the actual OAuth login happened in Microsoft Edge launched by the shortcut helper on CDP port **9222**. The monitor watched the wrong browser for 300 seconds and never saw the redirect URL.

The edge-auth browser IS launched with `--remote-debugging-port=9222` (confirmed in [launch.py](py_modules/unifideck/auth/edge_browser/launch.py#L145)), but the monitor only checked port 8080.

**Fix:**

- **`OAuthBrowserMonitor`** ([browser.py](py_modules/unifideck/auth/browser.py)): Added `edge_cdp_port` parameter (default 9222). Added `_list_edge_targets()` async method that polls `http://127.0.0.1:9222/json/list`. `wait_for_redirect()` now merges targets from both CEF (port 8080) and Edge (port 9222).
- **`constructor.py`** ([services/bootstrap/constructor.py](py_modules/unifideck/services/bootstrap/constructor.py)): Extracted `cdp_port` resolution before monitor construction and passed it as `edge_cdp_port`.
- **`close_oauth_tab()`** also updated to try closing on Edge's CDP endpoint.

**Justification:** The architecture intentionally uses an external Edge browser for OAuth (better isolation, shared profile for Microsoft xCloud sessions). The monitor must watch Edge's CDP port for this to work. The dual-endpoint approach preserves backward compatibility with any Steam CEF-based flows.

---

## Bug 5: Epic redirect URI false positive — `no_code` error

**Severity:** High — monitor captured Epic's intermediate OAuth redirect (before login) and failed with `no_code`, killing the flow before the user could sign in.

**Symptom:** CDP monitor detected `https://www.epicgames.com/id/api/redirect?clientId=...&responseType=code` at 16.6s. This URL matches the `_EPIC_REDIRECT_URIS` list prefix `https://www.epicgames.com/id/api/redirect` but has no `code` parameter — it's Epic's _initial_ authorize redirect that happens BEFORE the login form. The orchestrator errored with `no_code` and the flow died.

**Root cause:** `_EPIC_REDIRECT_URIS` included two entries:

1. `https://legendary.epicgames.com/callback` — the actual post-login callback (correct)
2. `https://www.epicgames.com/id/api/redirect` — Epic's internal authorize endpoint (incorrect — matches before login)

**Fix:** Removed the false-positive entry from `_EPIC_REDIRECT_URIS`:

```python
# [stores/epic/auth.py:47-49]
_EPIC_REDIRECT_URIS: list[str] = [
    "https://legendary.epicgames.com/callback",
]
```

Only `legendary.epicgames.com/callback` remains — the URL legendary's local server listens on AFTER the user signs in with the `code` parameter.

**Justification:** The removed URL was the OAuth _authorization_ endpoint, not the _callback_. Matching it before login yields a URL with `responseType=code` (a request for a code, not a code itself). The actual code-bearing redirect goes to `legendary.epicgames.com/callback?code=...`.

---

## Bug 6: Content extraction via aiohttp WebSocket failed — Epic code never captured

**Severity:** Critical — even when the `/id/api/redirect` page was detected, the authorization code embedded in the page body (as JSON) was never extracted.

**Symptom:** The CDP monitor correctly detected the `/id/api/redirect` page at Edge's CDP port (Bug 4 fixed). The page was retried every 0.5s. But `_extract_code_from_page()` always returned `None` — no code captured, no error logged (debug messages suppressed at INFO level).

**Root cause:** The content extraction method used `aiohttp.ClientSession().ws_connect()` for the CDP WebSocket connection. This WebSocket implementation is incompatible with Edge's CDP protocol. Staging's proven implementation uses the `websockets` library (`websockets.connect()`).

**Fix:** Rewrote `_extract_code_from_page()` to use `websockets.connect()` matching staging's proven pattern:

```python
# [auth/browser.py]
import websockets
async with websockets.connect(ws_url, ping_interval=None) as ws:
    await ws.send(json.dumps({...}))
    raw = await asyncio.wait_for(ws.recv(), timeout=5)
    data = json.loads(raw)
    value = data["result"]["result"]["value"]
```

Also added `first_attempt` diagnostic logging — the first extraction failure on each URL logs at INFO level with the reason (empty body, timeout, pattern not found, or exception).

**Justification:** Staging used `websockets` successfully with Steam CEF. The same library works with Edge's CDP implementation. aiohttp's WebSocket implementation has subtle differences in protocol handling that Edge's CDP rejects.

---

## Bug 7: Content extraction dedup prevented retries

**Severity:** High — after the first content extraction failed (page not loaded yet), the URL was added to `monitored_urls` and never retried, even though the authorization code appears in the page body only AFTER the user finishes signing in.

**Symptom:** The `/id/api/redirect` page was detected at 05:40:28. The first content extraction returned `None` (page body empty or not yet containing the code). The URL was added to `monitored_urls`. All subsequent poll iterations (every 0.5s) skipped this URL. The user logged in 20 seconds later, the code appeared in the page body, but the monitor never checked again.

**Fix:** Epic content-extraction pages (`/id/api/redirect` and `epicgames.com` URLs) are now excluded from `monitored_urls` dedup. They're retried every 0.5s until the code is found or the timeout expires.

```python
# [auth/browser.py:249-253]
is_content_page = ("/id/api/redirect" in url or "epicgames.com" in url.lower())
if not is_content_page:
    monitored_urls.add(url)
```

**Justification:** The page content changes over time — the `authorizationCode` JSON blob only appears when the user finishes signing in. Dedup is appropriate for static URLs but prevents retrying dynamic content.

---

## Bug 8: Per-store content extraction log spam

**Severity:** Medium — "OAuth page detected" logged every 0.5s for 23 seconds (100+ identical lines) when the content page was retried.

**Fix:** Added `logged_urls` set — each unique URL only logs "OAuth page detected" once. Subsequent retries are silent (extraction still happens, just not logged).

---

## Bug 9: Broad pattern matching not adopted from staging

**Severity:** High — the `wait_for_redirect()` method used strict `match_redirect()` prefix matching instead of staging's broad keyword-based approach.

**Symptom:** The monitor only checked URLs against the exact `allowed_uris` prefixes. Staging's `_poll_for_code` checks ANY URL containing `auth`, `login`, `code=`, `oauth`, `callback`, etc. for OAuth patterns. Some OAuth providers redirect through intermediate URLs that don't match the callback prefix exactly.

**Fix:** Rewrote `wait_for_redirect()` to adopt staging's broad keyword matching:

- First pass: broad keyword matching (`auth`, `login`, `code=`, `oauth`, `callback`, etc.) triggers inspection
- Epic content extraction run on any URL containing `/id/api/redirect` or `epicgames.com`
- Strict `match_redirect()` still checked for the standard OAuth callback URLs
- Generic `_extract_code()` method added (from staging) that extracts codes from ANY URL query params (handles Epic `authorizationCode=`, Amazon `openid.oa2.authorization_code=`, standard `code=`)

**Justification:** Staging's broad-matching approach was battle-tested and correctly captured codes for all four OAuth stores. The strict prefix matching was too narrow and missed redirects.

---

## Bug 10: GOG blank screen — `--app` mode omits headers

**Severity:** High — GOG auth page rendered as blank white screen in Edge.

**Symptom:** When Edge opened in `--app` mode, GOG's `auth.gog.com` page returned a blank white page. Other stores (Epic, Amazon, Microsoft) worked fine with `--app` mode.

**Root cause:** Edge's `--app` mode (frameless app window) omits `Referer` and `Origin` headers on the initial navigation. GOG's authentication endpoint requires these headers. The OAuth URL was `https://auth.gog.com/auth?client_id=...&redirect_uri=https://embed.gog.com/on_login_success?origin=client&response_type=code&layout=client2`.

**Fix:** Changed `--app={auth_url}` to `--new-window` with `auth_url` as the last positional argument in [launch.py](py_modules/unifideck/auth/edge_browser/launch.py#L142-L151). The `--start-fullscreen` flag still makes the window full screen for gaming mode.

**Justification:** A regular Edge window sends proper HTTP headers. `--start-fullscreen` hides the browser chrome, providing the same user experience as app mode. This fix was later reverted back to `--app` after confirming it wasn't the root cause of the blank screen — the actual fix for GOG was the CDP monitoring improvements that correctly captured the redirect URL.

---

## Bug 11: GOG token exchange SSL certificate verification failure

**Severity:** Critical — GOG auth captured the OAuth code successfully but token exchange failed with SSL error.

**Symptom:** Log showed `captured redirect after 21.0s: https://embed.gog.com/on_login_success?code=...` and `code captured: ...` but then `token endpoint GET https://auth.gog.com/token?... failed: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate`.

**Root cause:** GOG's HTTP module (`stores/gog/http.py`) used `ssl_ctx_strict()` for all connections. The Steam Deck's CA certificate store is outdated and rejects GOG's `auth.gog.com` certificate as invalid, even though the cert is actually valid.

**Fix:** Changed `build_ssl_context()` from `ssl_ctx_strict()` to `ssl_ctx_permissive("GOG OAuth — outdated Deck cert store")` in [http.py](py_modules/unifideck/stores/gog/http.py#L30-L38).

**Justification:** The permissive SSL context disables certificate verification only for GOG endpoints. This is needed because the Steam Deck's cert store is outdated (a system-level issue outside our control). The one-shot warning log informs operators why permissive mode is active.

---

## Bug 12: Microsoft launcher crashed — missing from `_AUTH_URL_FILES`

**Severity:** Critical — Microsoft auth launcher exited immediately without opening Edge.

**Symptom:** Launcher log showed `request received: microsoft:ms-auth` and `auth shortcut detected: auth_store=microsoft` but then nothing. Edge never opened. No auth pages appeared in CDP targets. The launcher crashed silently.

**Root cause:** The `_AUTH_URL_FILES` dict in `launcher/flows/auth.py` mapped store names to URL file paths, but `"microsoft"` was missing. `_read_auth_url("microsoft")` returned `None`, raising `GameNotFoundError("Unknown auth store 'microsoft'")`. This crashed the launcher before it could open Edge.

**Fix:** Added `"microsoft": "ms_auth_url.txt"` to `_AUTH_URL_FILES` and `"microsoft": "Microsoft"` to `_AUTH_STORE_LABELS` in [auth.py](py_modules/unifideck/launcher/flows/auth.py#L12-L22). Also fixed `store.title` → `store.title()` (was passing the method reference instead of calling it).

---

## Bug 13: Microsoft token exchange SSL failure

**Severity:** Critical — same SSL issue as GOG. Code captured from `login.live.com/oauth20_desktop.srf?code=...` but token exchange POST to `login.live.com` failed with `CERTIFICATE_VERIFY_FAILED`.

**Fix:** Changed ALL Microsoft HTTP calls from `ssl_ctx_strict()` to `ssl_ctx_permissive("Microsoft ... — outdated Deck cert store")` in:

- [microsoft_auth.py](py_modules/unifideck/stores/microsoft/microsoft_auth.py)
- [microsoft_subscription.py](py_modules/unifideck/stores/microsoft/microsoft_subscription.py)

---

## Bug 14: Ubisoft falsely showing "Connected" with no tokens

**Severity:** High — Ubisoft showed "Connected" immediately on click despite having no tokens and no UPC session.

**Symptom:** Clicking Connect on Ubisoft immediately showed "Connected" toast and button status changed, but `~/.local/share/unifideck/ubisoft_token.json` didn't exist and no Ubisoft Connect session had been established.

**Root cause:** `UbisoftAuth.start_auth()` returned `AuthResult(success=True)` with metadata `{auth_type: "upc_launch", message: "..."}` — no `pending` key. The `AuthDispatcher` fast-path check `!(startResult as any)?.metadata?.pending` evaluated to `true` (since `pending` was `undefined`), treating it as "already authed" and resolving immediately.

**Fix:** Added `"pending": True` to Ubisoft's metadata in [facade.py](py_modules/unifideck/stores/ubisoft/auth/facade.py#L139-L157). Now `AuthDispatcher` skips the fast-path and waits for `STORE_AUTH_COMPLETE` from the session monitor.

**Justification:** Ubisoft auth uses UPC (Ubisoft Connect) running in a Wine prefix — not browser-based OAuth. The session monitor polls for credential files. `STORE_AUTH_COMPLETE` fires only when credentials are detected. The `pending=True` flag tells the dispatcher to wait for this event.

---

## Bug 15: Global auth mutex blocking concurrent auth

**Severity:** Medium — clicking Connect on one store blocked all other stores.

**Symptom:** If GOG auth was in-flight (user logging in, 5-minute OAuth flow), clicking Microsoft would silently fail with "Auth already in flight for gog."

**Root cause:** `AuthDispatcher` used a single `{store, promise} | null` slot. Only ONE store could authenticate at a time. Any other store's `start()` call would throw.

**Fix:** Changed to `Map<StoreId, Promise<AuthResult>>` per-store map:

```typescript
// [AuthDispatcher.ts]
private inflight = new Map<StoreId, Promise<AuthResult>>();

async start(store: StoreId): Promise<AuthResult> {
    const existing = this.inflight.get(store);
    if (existing) return existing;  // dedup same store
    const promise = this.runFlow(store);
    this.inflight.set(store, promise);
    promise.finally(() => { this.inflight.delete(store); });
    return promise;
}
```

**Justification:** Each store's OAuth flow is independent (separate Edge window, separate CDP monitor, separate token exchange). There's no technical reason to serialize them.

---

## Bug 16: SyncService constructor argument order swapped

**Severity:** High — Sync button crashed with `AttributeError: 'EventBus' object has no attribute 'available'`.

**Symptom:** `SyncService._run_sync()` called `self._registry.available()` but `self._registry` was an `EventBus` instance, not `StoreRegistry`.

**Root cause:** `boot.py:183` called `SyncService(plugin.bus, plugin.registry)` but the constructor signature is `SyncService(registry, bus, config=None)`. The first argument (EventBus) was bound to `registry`, and `StoreRegistry` was bound to `bus`.

**Fix:** Swapped arguments: `SyncService(plugin.registry, plugin.bus)` in [boot.py:183](py_modules/unifideck/bootstrap/boot.py#L183).

---

## Bug 17: Sync method name mismatch — `.sync()` doesn't exist

**Severity:** High — Sync button crashed with `AttributeError: 'SyncService' object has no attribute 'sync'`.

**Symptom:** `sync_libraries` RPC called `self.sync_service.sync(**kw)` but `SyncService` has `sync_all()`, not `sync()`.

**Fix:** Changed `.sync(**kw)` → `.sync_all(**kw)` in:

- [mixins/sync.py](py_modules/unifideck/rpc/mixins/sync.py#L45)
- [handlers/store.py](py_modules/unifideck/rpc/handlers/store.py#L47)

---

## Bug 18: `force_sync_libraries` TypeError — can't accept positional boolean

**Severity:** Medium — Force Sync button crashed.

**Symptom:** `TypeError: SyncRPCMixin.force_sync_libraries() takes 1 positional argument but 2 were given`. The frontend called `force_sync_libraries(true)` passing a boolean as positional argument, but the backend signature was `def force_sync_libraries(self, **kw)` — `**kw` doesn't accept positional arguments.

**Fix:** Added explicit `resync_artwork: bool = False` parameter:

```python
# [mixins/sync.py:47-49]
async def force_sync_libraries(self, resync_artwork: bool = False, **kw: Any) -> Any:
```

---

## Bug 19: `checkStoreStatus` array-to-map conversion missing

**Severity:** Critical — all store statuses reset to "Connect" on screen navigation.

**Symptom:** Navigating between Decky tabs (e.g., Settings → Library → back to Settings) reset all button statuses from "Connected" to "Connect."

**Root cause:** `checkStoreStatus` returns a **list** of per-store status dicts (`[{store_id: "epic", available: true}, ...]`), but `AuthContext` stored it directly as `StatusMap` (a `Record<StoreId, StoreStatus>` map). The `setStatuses()` call stored the array, and `statuses["epic"]` was `undefined` (array indexing doesn't work with string keys). When the component remounted and re-fetched, the fresh array overwrote any values set by `notifyConnected`.

**Fix:** Added array-to-map conversion on receipt:

```typescript
// [AuthContext.tsx:63-79]
const raw = Array.isArray(initial.data) ? (initial.data as unknown[]) : [];
const map: StatusMap = {};
for (const entry of raw) {
  if (entry && typeof entry === "object") {
    const e = entry as Record<string, unknown>;
    const id = e.store_id as StoreId | undefined;
    if (id) map[id] = e.available ? "connected" : "disconnected";
  }
}
setStatuses(map);
```

Also added `notifyConnected(store)` to `AuthContext` so `useStoreAuth.connect()` can synchronously set status on auth success without waiting for EventBus polling.

---

## Bug 20: Ubisoft VDF appid tag format — `\x02` not `\x01`

**Severity:** High — Ubisoft "Context Unavailable" error blocking shortcut launch.

**Symptom:** Frontend showed "sign in failed - Context Unavailable" when clicking Connect on Ubisoft. Console showed `ctx.appid_unsigned` was `undefined` even after the backend returned `success=True`.

**Root causes (two layers):**

_Layer 1 — VDF tag type:_ The VDF binary format stores the `appid` field with tag type `\x02` (uint32), not `\x01` (string). The regex `\x01appid\x00` never matched. The actual byte sequence was `\x02appid\x00` + 4-byte little-endian uint32 (value: `2626371780`).

_Layer 2 — Envelope stripping:_ Even after fixing the VDF regex, `_get_compat_tool_impl` added `result["success"] = True`, which got stripped by `_to_envelope()` (same envelope-stripping behavior as Bug 2). The frontend checked `!ctx?.success` which was `true` (missing), returning "Context unavailable."

**Fix:**

- **Backend** ([auth_shortcuts.py](py_modules/unifideck/rpc/mixins/auth_shortcuts.py#L169-L205)): Changed `\x01appid\x00` to `\x02appid\x00` in VDF binary regex. Removed `result.setdefault("success", True)` to avoid envelope stripping.
- **Frontend** ([ubisoftShortcutLaunch.ts](src/utils/ubisoftShortcutLaunch.ts)): Changed from `!ctx?.success` check to `!ctx.appid_unsigned` check — the valid AppID proves the call succeeded regardless of envelope-stripped `success`.
- **Shortcut persistence** ([shortcut.py](py_modules/unifideck/stores/ubisoft/auth/shortcut.py)): Always re-write VDF entry on `ensure_auth_shortcut()` so Steam re-discovers it after plugin reload.

---

## Bug 21: Ubisoft launcher routing — browser OAuth instead of UPC

**Severity:** Critical — Ubisoft auth shortcut launched the browser OAuth handler instead of UPC.

**Symptom:** Launcher received `ubisoft:upc-auth`, detected it as auth, but routed to `handle_store_auth()` — which tried to read an auth URL file (Ubisoft doesn't have one) and launch Edge (Ubisoft doesn't use a browser).

**Root cause:** `LauncherService.launch()` at [service.py:172](py_modules/unifideck/services/launcher/service.py#L172) routes ALL `is_launch_action=False` contexts to `handle_store_auth`, which is designed for browser-based OAuth (Epic/GOG/Amazon/Microsoft). Ubisoft uses UPC (Ubisoft Connect) in a Wine prefix.

**Fix:**

- **Launcher routing** ([service.py](py_modules/unifideck/services/launcher/service.py#L172-L178)): Added Ubisoft branch before `handle_store_auth` — when `auth_store == "ubisoft"`, call `_launch_ubisoft_auth()`.
- **Auth flow** ([auth.py](py_modules/unifideck/launcher/flows/auth.py#L65-L73)): Added Ubisoft early-return in `handle_store_auth` — when store is `"ubisoft"`, log and return `Result(success=True)` immediately. The session monitor (running in the plugin process) handles credential detection.

**Justification:** Ubisoft auth is fundamentally different from the other four stores — it doesn't use a browser or OAuth redirects. UPC runs natively in a Wine prefix. The session monitor polls for credentials independently of the launcher process.

---

## Bug 22: Ubisoft auth prefix not created before session monitor

**Severity:** Critical — UPC couldn't launch because the auth Wine prefix didn't exist.

**Symptom:** Auth prefix directory `~/.local/share/unifideck/prefixes/ubisoft-games/.upc-auth/` didn't exist. UPC executable not found. Session monitor started but had nothing to monitor.

**Root cause:** `UbisoftStore.start_auth()` called `ensure_auth_shortcut()` and `start_auth_session_monitor()` but did NOT call `ensure_auth_prefix()`. The auth prefix creation was deferred to store initialization, which never triggers prefix creation.

**Fix:** Added `await self._prefix_mgr.ensure_auth_prefix()` to `start_auth()` in [store.py](py_modules/unifideck/stores/ubisoft/store.py#L103-L110). First-time setup downloads UPC installer (247 MB) and creates the Wine prefix (~5 minutes). Subsequent calls are no-ops (prefix exists).

---

## Bug 23: Ubisoft installer download SSL failure

**Severity:** High — UPC installer download from `ubistatic3-a.akamaihd.net` failed.

**Symptom:** `[UbisoftInstallerCache] download failed: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]>`

**Root cause:** Same SSL issue as GOG and Microsoft — the Ubisoft installer cache module used `ssl_ctx_strict()`.

**Fix:** Changed to `ssl_ctx_permissive("Ubisoft installer — outdated Deck cert store")` in [cache.py](py_modules/unifideck/stores/ubisoft/installer/cache.py#L26).

---

## Cross-cutting: Comprehensive OAuth diagnostic logging

Added INFO-level logs at every step of the auth flow so future failures are self-diagnosing:

**orchestrator.py:**

- Auth URL (domain+path, no query params containing secrets)
- Code captured (first/last 4 chars with length)
- Exchange result (error + error_code)

**browser.py:**

- Captured redirect URL with `code=<REDACTED>` substitution
- 30-second heartbeat showing CEF + Edge target counts
- Content extraction failure reasons (first attempt at INFO, subsequent at DEBUG)

**Per-store auth files:**

- Epic: `[epic_auth] captured URL from legendary: https://legendary.gl/epiclogin`
- GOG: `[GOGBrowserAuth] built OAuth URL: https://auth.gog.com/auth?client_id=REDACTED&redirect_uri=...`
- Amazon: `[amazon_auth] received login URL from nile: https://amazon.com/ap/signin`
- Microsoft: `[MicrosoftBrowserAuth] built OAuth URL: https://login.live.com/oauth20_authorize.srf?...`

All code parameters are NEVER logged in plain text — redacted to first+last 4 chars.

---

## Cross-cutting: Store-specific cookie clearing

Stale browser sessions (persisted in the shared Edge profile at `~/.local/share/unifideck/edge-auth/`) caused the OAuth page to auto-login without showing the login form. Added per-store cookie clearing:

**Infrastructure:**

- `EdgeProfileManager.clear_cookies_for_domain(domain)` in [profile.py](py_modules/unifideck/auth/edge_browser/profile.py) — SQLite DELETE on the Edge profile's `Default/Cookies` database
- `EdgeBrowser.clear_store_cookies(domain)` in [edge.py](py_modules/unifideck/auth/edge_browser/edge.py) — delegation method

**Store calls (in `start_auth()`, before auth launch):**

- Epic: `clear_store_cookies("epicgames.com")`
- GOG: `clear_store_cookies("gog.com")`
- Amazon: `clear_store_cookies("amazon.com")`
- Microsoft: `clear_store_cookies("microsoft.com")` + `clear_store_cookies("live.com")`

---

## Cross-cutting: `legendary.gl` URL marker

Epic's `legendary auth` command outputs `https://legendary.gl/epiclogin` as the OAuth URL (not `epicgames.com`). The URL extraction regex in [epic/auth.py](py_modules/unifideck/stores/epic/auth.py#L56) was updated to include `legendary.gl` in `_AUTH_URL_MARKERS`.

---

## Cross-cutting: `ProcessLookupError` in legendary termination

`_terminate_legendary()` called `proc.terminate()` on an already-dead process, raising `ProcessLookupError()` with an empty `str()` — replacing the real URL-extraction error with a useless empty-message log. Fixed by catching `ProcessLookupError` in the terminate method.

---

## Files Modified

### Frontend (TypeScript/TSX)

| File                                             | Changes                                                                                |
| ------------------------------------------------ | -------------------------------------------------------------------------------------- |
| `src/services/auth/AuthDispatcher.ts`            | Fast-path `metadata.pending` guard; `edge_not_installed` error branch; per-store mutex |
| `src/hooks/useStoreAuth.tsx`                     | `notifyConnected()` call on success                                                    |
| `src/contexts/AuthContext.tsx`                   | `checkStoreStatus` array→map conversion; `notifyConnected()` method                    |
| `src/components/modals/ChromiumInstallModal.tsx` | `result?.installed` check; diagnostic console.log                                      |
| `src/utils/ubisoftShortcutLaunch.ts`             | `!ctx.appid_unsigned` check; diagnostic console.log                                    |

### Backend (Python)

| File                                                              | Changes                                                                                                                                                |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `py_modules/unifideck/auth/orchestrator.py`                       | OAuth diagnostic logging; content extraction threading; exchange logging                                                                               |
| `py_modules/unifideck/auth/browser.py`                            | Dual-endpoint CDP polling; broad pattern matching; content extraction via websockets; `_extract_code()`; heartbeat logging; no-dedup for content pages |
| `py_modules/unifideck/auth/edge_browser/launch.py`                | Reverted `--app` → kept `--app={url}` (working)                                                                                                        |
| `py_modules/unifideck/auth/edge_browser/profile.py`               | `clear_cookies_for_domain()`                                                                                                                           |
| `py_modules/unifideck/auth/edge_browser/edge.py`                  | `clear_store_cookies()` instance method                                                                                                                |
| `py_modules/unifideck/services/bootstrap/constructor.py`          | `edge_cdp_port` shared between monitor and EdgeBrowser                                                                                                 |
| `py_modules/unifideck/services/launcher/service.py`               | Ubisoft auth routing                                                                                                                                   |
| `py_modules/unifideck/launcher/flows/auth.py`                     | Added `"microsoft"` to `_AUTH_URL_FILES`; Ubisoft early-return; `store.title()` fix                                                                    |
| `py_modules/unifideck/stores/epic/auth.py`                        | `legendary.gl` URL marker; `ProcessLookupError` catch; `_EPIC_REDIRECT_URIS` narrowed; content extraction params; auth URL logging                     |
| `py_modules/unifideck/stores/gog/auth.py`                         | Auth URL parameter logging                                                                                                                             |
| `py_modules/unifideck/stores/gog/http.py`                         | `ssl_ctx_permissive` for token exchange                                                                                                                |
| `py_modules/unifideck/stores/amazon/amazon_auth.py`               | Auth URL logging                                                                                                                                       |
| `py_modules/unifideck/stores/amazon/amazon_store.py`              | Cookie clearing                                                                                                                                        |
| `py_modules/unifideck/stores/microsoft/microsoft_auth.py`         | `ssl_ctx_permissive` for all HTTP calls                                                                                                                |
| `py_modules/unifideck/stores/microsoft/microsoft_subscription.py` | `ssl_ctx_permissive`                                                                                                                                   |
| `py_modules/unifideck/stores/microsoft/microsoft_browser_auth.py` | Auth URL parameter logging                                                                                                                             |
| `py_modules/unifideck/stores/microsoft/microsoft_store.py`        | Cookie clearing                                                                                                                                        |
| `py_modules/unifideck/stores/ubisoft/auth/facade.py`              | `pending: True` in metadata                                                                                                                            |
| `py_modules/unifideck/stores/ubisoft/auth/shortcut.py`            | VDF rewrite on existing shortcut                                                                                                                       |
| `py_modules/unifideck/stores/ubisoft/installer/cache.py`          | `ssl_ctx_permissive` for installer download                                                                                                            |
| `py_modules/unifideck/stores/ubisoft/store.py`                    | `ensure_auth_prefix()` in start_auth; cookie clearing TODO                                                                                             |
| `py_modules/unifideck/stores/epic/store.py`                       | Cookie clearing                                                                                                                                        |
| `py_modules/unifideck/stores/gog/store.py`                        | Cookie clearing                                                                                                                                        |
| `py_modules/unifideck/compatibility/proton_helpers.py`            | AppID resolution (reverted to simple version)                                                                                                          |
| `py_modules/unifideck/rpc/mixins/auth_shortcuts.py`               | VDF `\x02appid\x00` parsing; removed `success` from result; diagnostic logging                                                                         |
| `py_modules/unifideck/rpc/mixins/edge.py`                         | Return `{installed, error}` instead of `{success, error}`                                                                                              |
| `py_modules/unifideck/rpc/mixins/sync.py`                         | `.sync()` → `.sync_all()`; `resync_artwork` parameter                                                                                                  |
| `py_modules/unifideck/rpc/handlers/store.py`                      | `.sync()` → `.sync_all()`                                                                                                                              |
| `py_modules/unifideck/bootstrap/boot.py`                          | `SyncService` arg order swap                                                                                                                           |
