# Post-Merge Bug Fixes — PR #245 (Microsoft Store)

> **🗄️ ARCHIVED (2026-06-22) — historical postmortem.** A point-in-time record of bugs
> fixed after the PR #245 merge; the fixes are in the code. Kept for context, not
> maintained.

## Regressions & Pre-existing Issues

---

### BUG-1: Ubisoft Connect Shows "Sign in" After Successful Login

- **Severity:** HIGH (Regression)
- **Symptom:** StoreConnections shows "Sign in" for Ubisoft even though token file exists and user logged in successfully
- **Root Cause:** `validate_ticket()` calls `PUT /v3/profiles/sessions` with a 5s timeout. If the Ubisoft API returns non-200 (e.g. ticket expired between sessions), `is_available()` returns `False`. The token refresh (`start_token_refresh()`) runs on plugin init but may not complete before the first `check_store_status()` call from the frontend.
- **Token file:** `~/.local/share/unifideck/ubisoft_token.json` (exists, has valid structure)
- **Files:**
  - `py_modules/unifideck/stores/ubisoft_api.py:437-490` — `validate_ticket()`
  - `py_modules/unifideck/stores/ubisoft.py:151-164` — `is_available()`
  - `main.py:5924-5990` — `check_store_status()`
- **Planned Fix:**
  1. In `is_available()`, if `validate_ticket()` fails, attempt a token refresh before returning False
  2. Add a `rememberMeTicket` refresh fallback in `validate_ticket()` when 401 is returned
  3. Ensure `start_token_refresh()` completes (or at least starts) before first status check

---

### BUG-2: SteamGridDB Artwork Download Stuck (0/554)

- **Severity:** HIGH (Pre-existing)
- **Symptom:** Sync shows "Looking up 548 games on SteamGridDB..." but stays at 0/554 synced indefinitely
- **Root Cause:** `asyncio.gather()` missing `return_exceptions=True` in two locations. When any single SteamGridDB lookup throws an exception, the entire gather crashes and the sync hangs silently.
- **Files:**
  - `main.py:2872` — Regular sync: `asyncio.gather(*[limited_lookup(g) for g in ...])`
  - `main.py:3542` — Force sync: same pattern
  - All other 8 gather calls in the file correctly use `return_exceptions=True`
- **Planned Fix:**
  1. Add `return_exceptions=True` to both gather calls
  2. Add post-gather exception logging for failed lookups
  3. Add progress counter increments even on failure so UI updates

---

### BUG-3: Microsoft Login Shows "Page Not Normally Shown"

- **Severity:** CRITICAL (PR Bug)
- **Symptom:** After entering Microsoft credentials, browser shows "You have reached a page that is not normally shown. Microsoft will never ask you to copy or share this URL."
- **Root Cause:** OAuth `redirect_uri` is `https://login.live.com/oauth20_desktop.srf` — this is Microsoft's internal desktop OAuth endpoint not meant for browser navigation. The CDP code tries to intercept the auth code from network events, but the browser loads the redirect page first, causing Microsoft to show the error.
- **Files:**
  - `defaults/settings.json` — `redirect_uri` setting
  - `py_modules/unifideck/stores/microsoft_auth.py` — Auth flow
  - `py_modules/unifideck/stores/microsoft_cdp.py:52-79` — CDP code detection
- **Planned Fix:**
  1. Use `http://localhost:{port}/auth/callback` as redirect_uri
  2. Spin up a lightweight HTTP server on the callback port to capture the auth code
  3. Alternatively: intercept the redirect via CDP `Network.requestWillBeSent` BEFORE the browser navigates (using `Fetch.enable` with request interception)

---

### BUG-4: Chromium Not Loading in Gaming Mode

- **Severity:** CRITICAL (PR Bug)
- **Symptom:** Custom Chromium browser for Microsoft auth doesn't launch in Steam Deck gaming mode
- **Root Cause:** `clean_env()` in `microsoft_chromium.py` hardcodes `DISPLAY=:0`. Gaming mode uses gamescope with `DISPLAY=:1` or `:100`.
- **Files:**
  - `py_modules/unifideck/stores/microsoft_chromium.py:64-91` — `clean_env()`
  - `py_modules/unifideck/stores/microsoft_chromium.py:184-233` — `launch_auth()`
- **Planned Fix:**
  1. Preserve `DISPLAY` from parent environment if set (don't override)
  2. Only default to `:0` if `DISPLAY` is completely absent
  3. Also preserve `WAYLAND_DISPLAY` for Wayland sessions
  4. Detect gamescope display via `/proc` or `GAMESCOPE_DISPLAY` env var

---

### BUG-5: File Handle Leak in Chromium Launch

- **Severity:** CRITICAL (PR Bug)
- **Symptom:** File handle for `chromium-auth.log` opened but never closed after Popen
- **File:** `py_modules/unifideck/stores/microsoft_chromium.py:218-233`
- **Planned Fix:** Use `try/finally` to close `stderr_fh` after Popen completes, or track it as instance variable for cleanup in `stop()`

---

### BUG-6: SSL Verification Disabled Fallback

- **Severity:** CRITICAL (PR Bug)
- **Symptom:** If `certifi` is not installed, SSL certificate verification is silently disabled for Microsoft auth endpoints, enabling MITM attacks
- **File:** `py_modules/unifideck/stores/microsoft_auth.py:32-52`
- **Planned Fix:**
  1. Try system CA bundles before falling back to CERT_NONE
  2. Check `/etc/ssl/certs/ca-certificates.crt` (SteamOS path)
  3. Log prominently if SSL is disabled

---

### BUG-7: Race Condition — `_auth_monitor_task` Not Initialized

- **Severity:** CRITICAL (PR Bug)
- **Symptom:** `_auth_monitor_task` not set in `__init__()`, uses `hasattr()` check instead
- **File:** `py_modules/unifideck/stores/microsoft.py:64-90,261-263`
- **Planned Fix:** Initialize `self._auth_monitor_task: Optional[asyncio.Task] = None` in `__init__()`

---

### BUG-8: Locale Injection Without Sanitization (XSS)

- **Severity:** HIGH (PR Bug)
- **Symptom:** Locale string injected into JavaScript without escaping — potential XSS if locale contains quotes
- **Files:**
  - `py_modules/unifideck/utils/virtual_keyboard.py:329` — direct string replacement
  - `py_modules/unifideck/stores/microsoft_chromium.py:350` — f-string injection
- **Planned Fix:** Validate locale against BCP-47 regex, escape with `json.dumps()`

---

### BUG-9: Silent Failure on `removed=true`

- **Severity:** HIGH (PR Bug)
- **Symptom:** Microsoft auth silently fails when account gets `removed=true` response, no user feedback
- **File:** `py_modules/unifideck/stores/microsoft_cdp.py:159-178`
- **Planned Fix:** Add exponential backoff, proper error logging, return descriptive error to frontend

---

### BUG-10: Chromium Startup Timeout Too Short

- **Severity:** MEDIUM (PR Bug)
- **Symptom:** Fixed 2s timeout for Chromium CDP port check — too short for loaded systems
- **File:** `py_modules/unifideck/stores/microsoft_chromium.py:391-410`
- **Planned Fix:** Increase to 10s with polling loop checking CDP port availability

---

### BUG-11: Cookie DB Race Condition

- **Severity:** MEDIUM (PR Bug)
- **Symptom:** Direct SQLite modification of Chromium's cookie DB without locks
- **File:** `py_modules/unifideck/stores/microsoft_chromium.py:273-308`
- **Planned Fix:** Use `timeout=5` on SQLite connect, add proper error handling and rollback

---

### BUG-12: Hardcoded `/home/deck` Paths

- **Severity:** MEDIUM (PR Bug)
- **Symptom:** Chromium env setup assumes username is always `deck`
- **File:** `py_modules/unifideck/stores/microsoft_chromium.py:75,88-89`
- **Planned Fix:** Use `os.path.expanduser("~")` or `pathlib.Path.home()`

---

## Fix Priority Order

| Priority | Bug                         | Impact                                       | Status                                                                                                                                                |
| -------- | --------------------------- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1        | BUG-1 (Ubisoft auth)        | Users can't see Ubisoft as connected         | FIXED                                                                                                                                                 |
| 2        | BUG-2 (SteamGridDB stuck)   | Sync never completes for 500+ game libraries | FIXED                                                                                                                                                 |
| 3        | BUG-4 (Gaming mode display) | Microsoft auth unusable in gaming mode       | FIXED                                                                                                                                                 |
| 4        | BUG-5 (File handle leak)    | Resource leak on every auth attempt          | FIXED                                                                                                                                                 |
| 5        | BUG-7 (Task init race)      | Crash on rapid auth attempts                 | FIXED                                                                                                                                                 |
| 6        | BUG-6 (SSL fallback)        | Security vulnerability                       | FIXED                                                                                                                                                 |
| 7        | BUG-3 (OAuth redirect)      | Microsoft login "page not normally shown"    | INVESTIGATED — page is expected behavior; CDP intercepts code before page loads. Fixes to BUG-7/BUG-10 should resolve cases where CDP wasn't running. |
| 8        | BUG-8 (Locale XSS)          | Security vulnerability                       | FIXED                                                                                                                                                 |
| 9        | BUG-10 (CDP timeout)        | Auth fails on slow systems                   | FIXED                                                                                                                                                 |
| 10       | BUG-12 (Hardcoded paths)    | Breaks non-standard installs                 | FIXED                                                                                                                                                 |
| 11       | BUG-11 (Cookie DB)          | Potential DB corruption                      | FIXED                                                                                                                                                 |
| 12       | BUG-9 (removed=true)        | Silent auth failure                          | FIXED                                                                                                                                                 |
| 6        | BUG-6 (SSL fallback)        | Security vulnerability                       | Medium                                                                                                                                                |
| 7        | BUG-3 (OAuth redirect)      | Microsoft login doesn't complete             | High                                                                                                                                                  |
| 8        | BUG-8 (Locale XSS)          | Security vulnerability                       | Low                                                                                                                                                   |
| 9        | BUG-10 (CDP timeout)        | Auth fails on slow systems                   | Low                                                                                                                                                   |
| 10       | BUG-12 (Hardcoded paths)    | Breaks non-standard installs                 | Low                                                                                                                                                   |
| 11       | BUG-11 (Cookie DB)          | Potential DB corruption                      | Medium                                                                                                                                                |
| 12       | BUG-9 (removed=true)        | Silent auth failure                          | Medium                                                                                                                                                |

---

### BUG-13: Ubisoft Library Shows Only Free Games

- **Severity:** HIGH
- **Symptom:** After sync, only free/F2P games appear in Ubisoft library; purchased games are missing
- **Root Cause:** `_build_auto_visible_manifest()` creates a whitelist by corroborating GraphQL games against local UPC configuration cache. When no config cache exists (no `.upc-auth` prefix, fresh template), corroboration produces zero matches. The manifest then contains only F2P entries from the free games URL. `_apply_visible_manifest_filter()` uses this as a whitelist and drops all purchased games.
- **Files:**
  - `py_modules/unifideck/stores/ubisoft.py:1497-1514` -- `_build_auto_visible_manifest()`
- **Fix:** When no local corroboration data exists (no raw_config_entries, no parsed_entries, no corroborated matches), trust ALL GraphQL entries as the authoritative visible set instead of filtering down to an empty corroborated list. Free entries are still appended.
- **Status:** FIXED

---

### BUG-14: Microsoft CDP Auth Code Not Captured

- **Severity:** CRITICAL
- **Symptom:** Chromium launches, user completes login, but OAuth code is never captured. Browser stays open indefinitely.
- **Root Cause:** Multiple compounding issues:
  1. Default `cdp_port` was `8080` in `intercept_oauth_code()` and fallback in `_monitor_and_complete_auth()`, but Chromium uses `9222`
  2. Race condition: CDP monitoring started AFTER `wait_and_check_crash()` + `inject_virtual_keyboard()`, giving the OAuth redirect time to complete before any Network event listener was active
  3. Only `Network.requestWillBeSent` events were monitored; redirects may arrive as `Network.responseReceived` or `Page.frameNavigated`
  4. Blocking `ws.recv()` after `Network.enable` could consume the first real event
  5. No target type filtering -- could attach to browser target instead of page target
- **Files:**
  - `py_modules/unifideck/stores/microsoft_cdp.py` -- Complete rewrite of `intercept_oauth_code()`
  - `py_modules/unifideck/stores/microsoft.py:598-644` -- `_monitor_and_complete_auth()`
- **Fix:**
  1. Changed default CDP port from 8080 to 9222 everywhere
  2. Restructured `_monitor_and_complete_auth()` to start CDP interception task BEFORE waiting for crash check/keyboard injection
  3. Added `Page.enable` alongside `Network.enable` for comprehensive event coverage
  4. Added `Network.responseReceived` and `Page.frameNavigated` event handlers
  5. Removed blocking `recv()` after domain enable commands
  6. Added target type filtering (only attach to "page"/"webview" targets)
  7. Added code extraction from target URL during `/json` scanning (catches code even if CDP events miss it)
- **Status:** FIXED

---

### BUG-15: Microsoft Auth Only Launches Once Unless Chromium Profile Is Deleted

- **Severity:** CRITICAL
- **Symptom:** Microsoft sign-in opens once, but subsequent attempts do not reopen unless the user deletes Chromium files under `~/.local/share/unifideck/`
- **Root Cause:** Auth and xCloud share the same Chromium profile (`~/.local/share/unifideck/chromium-auth`). A previous auth browser can linger on the CDP port, and broken `SingletonLock` / `SingletonCookie` / `SingletonSocket` artifacts can keep the shared profile in a bad state. The previous `kill()` only terminated the tracked wrapper process, not the full Chromium process group.
- **Files:**
  - `py_modules/unifideck/stores/microsoft_chromium.py`
- **Fix:**
  1. Added `prepare_auth_launch()` to close any lingering auth browser via DevTools HTTP before relaunch
  2. Added stale `Singleton*` cleanup when the profile socket target is broken
  3. Launch Chromium in the same login session but its own process group (`os.setpgrp`)
  4. Terminate the full Chromium process group instead of only the wrapper process handle
- **Verification:** Real driver `test_real_ms_auth_relaunch.py` confirmed auth launches on the Microsoft authorize URL, closes cleanly, and relaunches immediately with the same shared profile.
- **Status:** FIXED

---

### BUG-16: Microsoft Auth Capture Fails When `websockets` Is Missing at Runtime

- **Severity:** HIGH
- **Symptom:** Auth monitor times out immediately with `websockets not available`, even though Chromium opens and the redirect eventually contains the OAuth code
- **Root Cause:** `intercept_oauth_code()` returned early if Python `websockets` was unavailable, so the plugin never attempted any fallback code capture path in runtimes where the dependency was missing.
- **Files:**
  - `py_modules/unifideck/stores/microsoft_cdp.py`
- **Fix:** Added a dependency-free fallback that polls DevTools `/json` targets and extracts the auth code directly from `oauth20_desktop.srf?code=...` target URLs. Also reordered scan logic so code URLs are still detected even after a target has been seen before.
- **Verification:** Local self-test with a fake DevTools server confirmed the polling fallback captures `test-code-123` without `websockets` installed.
- **Status:** FIXED

---

### BUG-17: Microsoft Appears Disconnected Even After Successful OAuth

- **Severity:** HIGH
- **Symptom:** Microsoft token file is written, but `check_store_status()` still reports `not_connected`
- **Root Cause:** `is_available()` required an `xbox.com` browser cookie and would call `logout()` when the browser session was missing, deleting valid Microsoft OAuth state even though the connector had a refresh token.
- **Files:**
  - `py_modules/unifideck/stores/microsoft.py`
- **Fix:** `is_available()` now treats the saved refresh token as the source of truth for connector auth state. Missing browser cookies are logged but no longer force a logout.
- **Verification:** Self-test confirmed `is_available()` returns `True` with a valid refresh token even when no Xbox cookie is present.
- **Status:** FIXED

---

### BUG-18: Microsoft Auth Chromium Does Not Appear in Steam Deck Gaming Mode

- **Severity:** CRITICAL
- **Symptom:** Microsoft sign-in may launch a Chromium process, but the auth window does not visibly appear in gaming mode / Steam frontend
- **Root Cause:** Microsoft auth launch was still using a Decky-service-centric environment and `start_new_session=True`. That combination can put Chromium outside the active Steam/gamescope session even though the process starts successfully. Unlike Ubisoft's launcher/auth flows, Microsoft was not discovering the real graphical session env from running Steam/gamescope processes, and it was not seeding Steam window env defaults to help gamescope surface the window.
- **Files:**
  - `py_modules/unifideck/stores/microsoft_chromium.py`
- **Fix:**
  1. Ported Ubisoft-style session env detection into `clean_env()` so Microsoft auth can recover `DISPLAY`, `WAYLAND_DISPLAY`, `GAMESCOPE_WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, `XDG_SESSION_TYPE`, and `XDG_CURRENT_DESKTOP` from live `steam` / `gamescope-session` / `gamescope` processes when Decky's own env lacks them
  2. Seeded `SteamGameId`, `SteamAppId`, and `STEAM_COMPAT_APP_ID` defaults so the auth window has Steam-visible window env in gaming mode
  3. Replaced `start_new_session=True` with `preexec_fn=os.setpgrp` so Chromium gets its own process group for reliable cleanup while staying in the same login session as Steam/gamescope
  4. Added a dedicated window class (`--class=unifideck-auth`) and expanded launch logging for display/wayland visibility debugging
- **Verification:** Local tests confirmed:
  - `clean_env()` recovers the active session env even after removing display/session vars from the current process env
  - launched Chromium now stays in the same session as the caller while getting its own process group
  - the real relaunch driver still opens Chromium on the Microsoft sign-in URL, closes it cleanly, and reopens it immediately with the shared profile
  - a temporary Chromium probe launched with the same Steam-session env and a dedicated app-window class was visible on `DISPLAY=:0`, with `WM_CLASS` showing `unifideck-auth-probe`
- **Status:** FIXED IN CODE -- X-visible probe passed; still requires on-device Steam frontend QA

---

### BUG-19: Repeated Microsoft Sign-In Requests and Stale Auth Cleanup Tear Down the Auth Window

- **Severity:** HIGH
- **Symptom:** Repeated Microsoft sign-in attempts close and relaunch Chromium every 1-2 seconds, making the auth window appear to never load or instantly disappear
- **Root Cause:** `start_auth()` always called `prepare_auth_launch()` + `launch_auth()` even if an auth session was already active. In practice, repeated frontend or user-triggered sign-in requests kept killing the existing Chromium auth window and replacing it before it could stabilize. There was also a backend race where an older `_auth_monitor_task` could still unwind and call `self._browser.kill()` after a newer launch had already replaced `self._browser.process`, making the fresh Chromium window vanish immediately. The frontend also had no debounce for in-progress Microsoft auth.
- **Files:**
  - `py_modules/unifideck/stores/microsoft.py`
  - `src/index.tsx`
- **Fix:**
  1. Backend guard: if the Microsoft auth monitor task is still running and the auth Chromium browser is alive, `start_auth()` now reuses the existing session instead of relaunching
  2. Added `ChromiumBrowser.is_running()` to detect an active auth browser
  3. `start_auth()` now cancels and awaits any stale auth monitor before preparing or launching a fresh Chromium session
  4. Auth monitor cleanup is session-scoped so stale monitor teardown cannot kill a newer Chromium auth process
  5. Frontend guard: Microsoft sign-in requests are now debounced in `src/index.tsx` so duplicate button presses are ignored until the current auth poll completes or errors out
- **Verification:** Temp stress test calling `start_auth()` five times in quick succession now keeps a single Chromium PID alive instead of spawning a relaunch storm. Additional targeted backend verification confirmed a canceled stale auth monitor no longer kills a newer Chromium launch during handoff.
- **Status:** FIXED

---

### BUG-20: Microsoft Auth Window Invisible in Gaming Mode

- **Severity:** CRITICAL
- **Symptom:** Microsoft sign-in launches Chromium via subprocess, but gamescope (Steam Deck's compositor in gaming mode) does not surface directly-launched windows. The auth window is completely invisible in gaming mode despite being detectable on X11.
- **Root Cause:** Epic, GOG, and Amazon all open auth URLs in Steam's built-in CEF browser via `window.open()` from the frontend. Steam's CEF popup is always visible in gaming mode because it is Steam's own window. Microsoft, however, launched an external Chromium process from the Python backend and returned `chromium_auth: true` to the frontend (skipping the `window.open()` popup path). Gamescope only surfaces windows from Steam's own processes or those launched through `SteamClient.Apps.RunGame()` -- a raw subprocess is invisible.
- **Files:**
  - `py_modules/unifideck/stores/microsoft.py`
- **Fix:**
  1. `start_auth()` now returns `url` (the OAuth URL) instead of `chromium_auth: true`
  2. Frontend uses the existing `window.open(authUrl, "_blank", ...)` popup path (same as Epic/GOG/Amazon)
  3. `_monitor_and_complete_auth()` now uses `CDPOAuthMonitor` on port 8080 (Steam's CEF) instead of `intercept_oauth_code` on port 9222 (Chromium)
  4. CDPOAuthMonitor already supports Microsoft OAuth code capture (URL polling + Fetch interception for `oauth20_desktop.srf`)
  5. After auth completes, CEF auth popup pages are closed via CDP
  6. Chromium is no longer launched for auth -- only for xCloud game streaming
  7. Removed now-unused `microsoft_cdp.intercept_oauth_code` import
- **Verification:** Python compile check passes. Frontend build passes. CDPOAuthMonitor's `_extract_code()` already handles Microsoft's `oauth20_desktop.srf?code=` redirect pattern. Frontend `window.open()` path at index.tsx:1287 is proven to work in gaming mode (used by Epic/GOG/Amazon).
- **Status:** FIXED IN CODE -- requires on-device gaming-mode QA
