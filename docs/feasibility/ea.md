# Feasibility Study: EA App store

**Verdict: FEASIBLE — launcher-wrapper archetype (Ubisoft template), with a materially better
library story than Ubisoft.** Every MVP step has a live, maintained reference implementation, and
the two historical blockers (blank WebView2 login window; ownership enumeration) are respectively
fixed and solved. On this Steam Deck (2026-07-03) the official installer ran **unattended to
completion** in a throwaway umu prefix and the EA App presented a fully rendered sign-in window on
first launch under stock GE-Proton11-1.

No open CLI exists (Heroic marked EA support wontfix over the undocumented download protocol), so
the client wrapper is the only viable install/launch path — exactly the archetype
`docs/ubisoft-store-spec.md` already documents.

## MVP-bar walkthrough

| Step | Status | Evidence |
|------|--------|----------|
| Login | Proven (live reference) | Standard OAuth2 code flow at `accounts.ea.com` (`client_id=JUNO_PC_CLIENT`), code scraped from the `qrc:///html/login_successful.html` redirect — identical shape to our Epic/GOG/Amazon Edge-CDP capture. Proven in the maintained GOG Galaxy plugin (BellezaEmporium/galaxy-integration-ead, v44.6 2026-05-04). Alternatively: log in inside the client window (renders fine — see prototype). |
| Enumerate owned games | Proven (live reference) | juno GraphQL, single endpoint `service-aggregation-layer.juno.ea.com/graphql`: `me { ownedGameProducts(...) }` returns OWNED entitlements (not just installed) with offer ids + slugs; `legacyOffers` maps to `contentId` for deeplinks. Same endpoint/queries Lutris `services/ea_app.py` ships today (GPL — re-derive, don't copy). JWT Bearer only; no client needed for enumeration. |
| Download/install | Proven (live reference + on-device) | Client drives downloads in-prefix. Trigger: `origin2://game/launch?offerIds=<contentId>&autoDownload=1` (download action is not implemented in EA App; launch+autoDownload is the working idiom — Lutris uses exactly this). Installer + protocol handler verified on-device (below). |
| Launch | Proven for non-kernel-AC titles | `origin2://game/launch?offerIds=<contentId>` with the client resident (client REQUIRED even offline → watchdog needed, same as UPC). Kernel anti-cheat titles (Javelin/EAAC: Battlefield, FC, Apex…) do not run under Proton — out of MVP scope; single-player catalog launches fine. |

Stretch: achievements exist via juno (the Galaxy plugin implements them); cloud saves are handled
client-internally per-title with no clean external API — both post-MVP.

## On-device prototype results (2026-07-03, scratch: `~/feasibility-scratch/ea/`)

- Official installer `origin-a.akamaihd.net/EA-Desktop-Client-Download/installer-releases/EAappInstaller.exe`
  (2.0 MB bootstrapper) downloaded and run under `umu-run` with `WINEPREFIX=<scratch>/ea/prefix`,
  `PROTONPATH=GE-Proton11-1`, **no flags and no interaction**: it downloaded the full client and
  installed `Program Files/Electronic Arts/EA Desktop/13.735.2.6250/` on its own (`/quiet` exists
  for headless installs — NonSteamLaunchers uses it).
- **Login window renders perfectly** — "Sign in to your EA Account" with Google/Facebook/Apple/Steam
  buttons, email field, NEXT. No blank WebView2. Screenshot: `~/feasibility-scratch/ea/logs/ea-window-1.png`.
  (Proton Experimental/11 shipped an explicit EA App fix in 2026-04 — Xalia 0.4.9; GE-Proton11-1
  evidently suffices.)
- `ProgramData/EA Desktop/530c11479fe252fc5aabc24935b9776d4900eb3ba58fdc271e0d6229413ad40e/`
  exists — the exact hash dir from the GameFinder IS-file spec (the encrypted library file lands
  here post-login).
- Prefix registry: `origin2://` → `EALauncher.exe "%1"` and `link2ea://` both registered —
  deeplink dispatch mechanism confirmed present.
- Prefix cost: ~2.5 GB installed client.

## Integration design (wrapper archetype, Ubisoft template — but simpler in two key ways)

1. **Library does NOT need the client or prefix**: juno GraphQL over HTTPS with a JWT captured by
   the existing Edge/CDP auth harness (`auth/orchestrator.py` pattern). Unlike Ubisoft (dead API →
   local binary parsing + Algolia UUID catalog), EA gives us owned games + names + offer ids
   server-side. No bespoke ID catalog required; `external_ids["ea"]` (offerId/slug) in unifiDB for
   exact matching.
2. **Install/launch mirror Ubisoft**: per-game (or single shared — decide in spec) prefix hosting
   EA Desktop; installs driven by `origin2://…autoDownload=1` deeplink → client UI does the
   download (`download_phase="manual"` indeterminate progress, the Ubisoft precedent); launch via
   the same deeplink with a resident-client watchdog (`pgrep`-gated like `_upc_process_alive`).
   New `launcher/proton/handlers/ea.py` + dispatch branch.
3. **Auth capture-back**: token from Edge capture is primary. `pc_sign` (hardware hash) is
   optional — the Galaxy plugin falls back to empty and still authenticates; running the capture
   inside the client prefix sidesteps it entirely if needed. Watch for extra 2FA challenges on
   headless logins (verify in follow-up).
4. **Installed-state detection**: parse `Program Files/EA Games/<title>/__Installer/installerdata.xml`
   (contentIDs) exactly as Lutris `EAAppGames` does — no IS decryption needed for MVP. IS-file
   AES decrypt in-prefix (GameFinder spec) stays an R&D option for offline detection only
   (GameFinder issue #71 "Wine support" is open; nobody has shipped it).

## Effort estimate

**Between Amazon and Ubisoft, much closer to Ubisoft-lite: roughly 15–25 files / 4–6k LOC.**
Cheaper than Ubisoft because: real ownership API (no binary catalog parsing, no UUID bridge, no
Algolia), documented deeplinks, and a login window that works. Cost drivers that remain: prefix
lifecycle + auth capture-back (the exact areas Ubisoft repeatedly re-broke), client auto-update
handling, install watchdogs, manual-phase install UX. Reuse `docs/ubisoft-store-spec.md` structure
and the `stores/ubisoft/prefix/` + `installer/window_probe.py` patterns directly.

## Risks

- **EA App self-updates aggressively** and periodically breaks its own prefix under Wine
  (NonSteamLaunchers carries a dedicated `repaireaapp` path). Plan a repair flow (re-run installer
  `/quiet` on breakage). This is the top operational risk.
- **juno schema drift**: EA killed the entire Origin plugin generation in the 2025 migration once
  already. The maintained Galaxy fork and Lutris both track it — budget maintenance like the
  Ubisoft integration.
- **ToS gray zone**: EA's User Agreement has broad anti-automation boilerplate (aimed at
  cheats/scrapers). No documented pattern of store-usage bans for Wine/alt-launcher users;
  EA officially supports EA titles on Deck. Same posture as the shipped Ubisoft integration.
- **Kernel anti-cheat titles** are not launchable under Proton (EA has announced intent for Linux
  Javelin support, not shipped). Consider a catalog flag to message this in UI rather than
  letting launches fail.
- **License hygiene**: the best reference (BellezaEmporium/galaxy-integration-ead) has NO license —
  re-derive endpoints/queries from Lutris (GPL, approach only) and our own capture, don't port code.

## OSS leverage

| Project | License | Health (2026-07-03) | Reuse |
|---------|---------|---------------------|-------|
| lutris/lutris `services/ea_app.py` (580 L) | GPL-3.0 | pushed 2026-07-03 | Canonical juno queries (`ownedGameProducts`, `legacyOffers`), token refresh trick (`ORIGIN_JS_SDK` prompt=none), `origin2://` idiom, installerdata.xml scan. The user already ported Lutris' Ubisoft library approach — same play here. |
| BellezaEmporium/galaxy-integration-ead | none (⚠) | v44.6 2026-05-04, pushed 2026-06-20 | OAuth params (`JUNO_PC_CLIENT`, redirect, token exchange), pc_sign fallback behavior — facts only, no code |
| moraroy/NonSteamLaunchers | MIT | pushed 2026-07-03, 4.3k★ | `/quiet` install under latest GE in dedicated prefix; `repaireaapp` self-heal pattern |
| erri120/GameFinder (EA Desktop wiki) | GPL-3.0 | pushed 2026-06-18 | IS-file AES-256-CBC spec (R&D path only); Wine key repro unproven (issue #71 open) |
| Heroic #352 (wontfix) | — | — | Confirms no-CLI reality; validates wrapper choice |

## Login-gated verification checklist (interactive follow-up)

1. Sign in inside the rendered client window (prefix retained at `~/feasibility-scratch/ea/prefix`).
2. Confirm `ProgramData/EA Desktop/<hash>/IS` materializes; attempt in-prefix decrypt per
   GameFinder spec (WMI values via `wine wmic`/registry) — R&D data point, not gating.
3. Capture the juno OAuth flow with a controlled browser (code → token exchange) and run the
   `ownedGameProducts` query → confirm the real owned library enumerates headlessly.
4. `origin2://game/launch?offerIds=<owned contentId>&autoDownload=1` → confirm client starts the
   download; then plain launch deeplink → confirm game boots. Note 2FA/captcha friction, if any.
5. Observe client auto-update behavior across a restart (the churn risk in vivo).

## Sources

Galaxy EAD plugin: github.com/BellezaEmporium/galaxy-integration-ead (`src/plugin.py`,
`src/backend.py`, `src/http_client.py`, `src/pcsign_hash.py`) · Lutris:
github.com/lutris/lutris `lutris/services/ea_app.py` · installer/silent flags:
github.com/moraroy/NonSteamLaunchers-On-Steam-Deck (`NonSteamLaunchers.sh`),
community.chocolatey.org/packages/ea-app · IS spec: github.com/erri120/GameFinder/wiki/EA-Desktop,
issue #71 · Proton fix: gamingonlinux.com 2026-04 (Xalia 0.4.9 in Proton Experimental/11 Beta) ·
offline-requires-client: EA forums KB 11897237 · Javelin/Linux status: gamingonlinux.com 2026-03 ·
ToS: ea.com/legal/user-agreement · Heroic wontfix: HeroicGamesLauncher issue #352.
On-device artifacts: `~/feasibility-scratch/ea/` (prefix with EA Desktop 13.735.2.6250,
`logs/ea-window-1.png`, `logs/install.log`).
