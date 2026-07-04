# Issue Tracker Triage — v0.7.0 release (all 110 open issues)

Triaged 2026-07-02 against the 0.7.0 codebase (commit c64dbe0) and the full 0.6.1→0.7.0 commit history. Every open issue is classified below.

**Legend** — Priority: P1 (user-blocking/frequent) → P3 (nice-to-have). Feasibility: High/Med/Low. Effort: S (<1 day) / M (days) / L (week+). Research: none / some / heavy.

**Summary** (all 110 accounted for): 23 close as fixed/shipped · 17 close as obsolete (stack rewritten) · 9 close as not fixable / out of scope / answered · 32 close-pending-retest on 0.7 · 26 keep as backlog · 3 need info. That is **49 closable immediately (45%)**, rising to ~74% as retests confirm.

Suggested closing-comment templates are at the bottom.

---

## A. Close now — FIXED or SHIPPED in 0.7 (23)

Confidently resolved by a specific 0.7 change. Close on release day citing the feature.

| # | Issue | What fixed it |
|---|-------|---------------|
| #153 | Self Updater (FR) | Shipped: in-plugin update checking + one-click install with progress |
| #325 | Playtime tracking | Shipped: local playtime tracking, stats/streaks UI, sync back to GOG/Epic |
| #111 | Cloud saves icon (FR) | Shipped: cloud-save status indicator + manual sync button on game page |
| #294 | Cloud save + achievement indicators (FR) | Shipped: both — cloud-save status UI and GOG/Epic achievements display |
| #119 | Select default Proton (FR) | Shipped: default is auto-installed latest GE-Proton; per-game via Steam's own Compatibility dropdown, persisted |
| #240 | Gear icon menu (FR) | Shipped: gear now opens Steam's real app menu (Manage/Properties) |
| #299 | Sort by Metacritic (FR) | Shipped: library facet enrichment — native Steam sorting/filters incl. review score now work for shortcuts |
| #89 | Unrelated/empty collections | Shipped: collections are opt-in (default off) + legacy cleanup/migration |
| #287 | Installed tab shows 5000+ items | Fixed: authoritative Unifideck-game filtering; installed filter no longer swallows third-party shortcuts |
| #293 | Metadata box breaks theme on non-Unifideck games | Fixed: native UI is no longer suppressed on non-managed shortcuts |
| #285 | Ubisoft games uninstall themselves / Proton resets every launch | Fixed: Ubisoft prefix lifecycle rework + Proton choice persistence + global-default compat tool no longer mis-adopted |
| #292 | Ubisoft sync 19/29 games | Fixed: UUID-catalog bridge recovers ownership-binary UUID entries |
| #75 | 700 orphan entries after uninstall | Fixed: orphan shortcut scanner + startup cleanup + hardened delete-all-data |
| #159 | Uninstalled game stays in "Recent Games" | Fixed: bogus last-played timestamps reset; shortcut removal cleans state |
| #194 | Install only registers if you stay on the page | Fixed: post-install registration moved into the download worker (event-driven, page-independent) |
| #148 | Stuck at "Installing Epic Games CLI…" | Fixed by design: all store CLIs now ship inside the plugin — no runtime CLI download exists anymore |
| #256 | Must resync after every sleep/shutdown | Fixed: library state persistence across restarts |
| #147 | Splinter Cell (GOG) launches dxconfig | Fixed: store-aware exe resolution + new "change executable" context-menu action for edge cases |
| #121 | [DEV] Ubisoft support proposal | Done: Ubisoft Connect shipped 0.6.0, overhauled 0.7 — thank contributor and close |
| #95 | Moondeck conflict (button removed on Steam games) | Fixed: Unifideck no longer touches non-managed titles' UI (`isUnifideckGame` gating) — verify once with Moondeck installed, then close |
| #4 | Install to a specific directory | Shipped: custom install locations (0.6.0) + full directory browser with drive selection (0.7) |
| #48 | Filter by installed/non-installed | Shipped: native Steam library filters now work for shortcuts (facet enrichment) + installed-state filter fix; visual grey-out remains open as #72 |
| #70 | Epic sync includes UE plugins/assets | Fixed in 0.5.1 ("Hide Epic Plugins") — issue predates it; close citing that release |

## B. Close now — OBSOLETE, superseded by the 0.7 rewrite (17)

All reported against the pre-0.7 auth/launch stack (CDP flow, "pretty-print" JSON page, black login screens) that no longer exists. The 0.7 AuthDispatcher rework fixed silent auth failures, RPC envelope handling, stale cookies, and premature window closure. Close with the "auth/launch stack rewritten" template; reopen only if reproduced on 0.7.

| # | Issue | Era |
|---|-------|-----|
| #96 | Epic login page won't load | 0.5.x auth |
| #101 | Can't sign in to GOG or Amazon (blank page) | 0.5.x auth |
| #105 | Paste password in desktop mode (FR) | pre-Edge flow; Edge auth window accepts normal input/paste — close, reopen if still blocked |
| #106 | Epic auth returns code but no exchangeCode | 0.5.x CDP flow |
| #135 | GOG black screen on sign-in | 0.5.x auth |
| #137 | Epic auth token not passed back ("pretty-print") | 0.5.x auth |
| #157 | Epic login page error -7 | 0.5.x auth |
| #160 | Epic login black screen / pretty-print | 0.5.x auth |
| #286 | Epic login broken on 0.6.1 (pretty-print) | 0.6.1 handoff bug, fixed in dispatcher rework |
| #288 | Kicked to Gaming Mode after entering email | window-lifecycle bug, fixed (app-lifetime monitoring) |
| #290 | Login window closes before password | same window-lifecycle fix |
| #302 | "Could not get auth URL" | 0.6.x auth |
| #284 | TypeError: Failed to fetch (plugin fails to load) | pre-0.7 frontend; entire frontend rebuilt |
| #83 | Epic/GOG games refuse to open (no logs, 0.5.x) | pre-0.7 launch stack |
| #51 | Plugin reverts to 0.3.0 on reboot | 0.4-era install/deploy quirk; packaging rebuilt and the self-updater now manages versions |
| #52 | Epic game crashes on launch (no details, 0.5.x) | pre-0.7 launch stack; reopen with `launches/<id>.game.log` if it recurs |
| #58 | "Not adding anything at all" (0.5.x, no details) | pre-0.7 sync stack |

## C. Close now — not fixable, out of scope, or answered (9)

| # | Issue | Reason |
|---|-------|--------|
| #187 | Fortnite unable to download | Not fixable: Fortnite requires Easy Anti-Cheat + official launcher; Legendary/third-party launchers cannot run it. Out of the plugin's control |
| #242 | "FAILED TO WRITE SHORTCUT" on Windows | Unsupported platform: Unifideck is a SteamOS/Linux Decky plugin; Windows isn't a target |
| #92 | Flagged as malicious on Windows/Defender | Answered: false positive on bundled store CLIs; plugin doesn't target Windows. Consider a FAQ entry, then close |
| #79 | TMNT 16:10 scaling (Legion Go S) | Not a plugin issue: game-engine resolution limitation; point to per-game gamescope resolution properties |
| #172 | Wine DLL overrides how-to | Answered: works via Steam launch options env passthrough (`WINEDLLOVERRIDES=... %command%`) — link `docs/launch-options.md`, close |
| #120 | lsfg command breaks launches | Answered: use env-var form (`LSFG=1 %command%`), not the legacy `~/lsfg` command form — link docs, close (same underlying doc as #172) |
| #230 | Stove store support (FR) | Out of scope: no Linux tooling/public API for Stove; note that store requests are tracked and close (or park in a pinned "store requests" issue) |
| #53 | Plugin on Windows (FR) | Unsupported platform (same call as #242): SteamOS/Linux is the target |
| #25 | Publish to Decky plugin store | Decided: distribution is GitHub + the new built-in self-updater (the outcome of the debate this issue references). Close as answered; revisit only if store policy needs change |

## D. Close pending retest on 0.7 (32)

Very likely fixed by 0.7 work but game- or setup-specific enough that we can't be certain. Recommended flow: comment asking the reporter to retest on 0.7.0, label `retest-0.7`, auto-close after ~3 weeks of silence.

| # | Issue | Why it's probably fixed |
|---|-------|------------------------|
| #298 | Ubisoft Wildlands not found | UUID catalog + dedup overhaul |
| #371 | Ubisoft games don't appear after sync | UUID bridge + steam-linked owned filter |
| #266 | Avatar (Ubisoft) not syncing | UUID catalog (key-redeemed titles were the classic victims) |
| #263 | Watch Dogs 2 + Quantum Break missing | Ubisoft half almost certainly fixed; Microsoft catalog half → verify |
| #155 | GOG cloud sync never works (no save_path) | Complete cloud-save rebuild with Ludusavi/PCGW path resolution |
| #171 | GOG saves not uploaded | Same rebuild |
| #319 | GOG force-download overwrote newer local saves | mtime-preservation guards added — retest to confirm the exact scenario |
| #231 | Death Stranding (Epic) no cloud saves | Epic save-path account-id fix |
| #226 | Streets of Rage 4 (GOG) won't open | Exe-resolution overhaul (+ custom exe fallback) |
| #248 | GOG DOSBox launches wrong exe | DOSBox-aware launch handler shipped |
| #107 | Dredge (GOG) DLC + reinstall failure | GOG install/repair overhaul (non-destructive reinstall) |
| #173 / #338 | Can't install DLC (Cyberpunk) | DLC auto-install for owned DLC; retest — if still gated, becomes backlog item |
| #103 | Very slow GOG downloads | gogdl updated + install pipeline rework; retest for speeds |
| #150 | No video (BINK) with default Proton | Default is now GE-Proton (ships the codecs) |
| #221 | Games run 100% CPU "outdated Proton" | Same — GE default replaces old fallback |
| #146 | Shadow of the Tomb Raider broken since 0.4 | Launch stack rewritten |
| #110 / #114 / #195 | Game launched once, then never again / won't start | Launch stack rewritten + exe re-resolution + per-launch logs now exist for real diagnosis |
| #318 | Legendary exits immediately at launch | Bundled legendary + launch rework |
| #291 | Keylocker black screen (offline handshake) | Epic offline mode + launch rework |
| #269 | Epic in-game purchases (EOS overlay) broken | Overlay handling preserved in new launch path — needs a purchase test |
| #289 | Microsoft QR code vanishes | Auth-window lifecycle fix |
| #297 | Microsoft sync dead-ends on warning page | Auth rework; MS flow changed |
| #99 | LEGO Star Wars (Epic) won't download | Old legendary; bundled current version |
| #368 | Flaky store sign-ins on Zotac Zone / SteamOS 3.8 | Auth rework + cross-distro handling; retest on 0.7 |
| #215 | Games not placed in their store collection | Collections rebuilt as opt-in with per-store curation — retest with collections enabled |
| #39 | TabMaster interop (all games land in "non-steam") | 0.7 native filters + opt-in per-store collections give TabMaster real grouping signals — retest with collections on |
| #55 | Epic login via PlayStation/3rd-party SSO fails | Auth now runs in a real Edge browser — third-party SSO flows should complete |
| #64 | Witcher 3 (Epic) "cannot find the launcher" | Exe re-resolution + Change Executable cover the wrong-entry-point class |
| #69 | Watch Dogs 2 (Epic) closes on launch | Uplay-for-Epic prerequisite flow + rewritten launch stack |

## E. Keep — actionable backlog (26 issues, 21 rows after dedup)

| # | Issue | Priority | Feasibility | Effort | Research | Notes |
|---|-------|----------|-------------|--------|----------|-------|
| #212 / #273 | Launch options wiped on restart/sync | **P1** | High | M | some | Real and current: reconcile rewrites LaunchOptions. Fix = merge user-added options instead of overwrite. Dup — merge #273 into #212 |
| #361 | RAM fills during download until crash | **P1** | Med | M | some | Suspect tmpfs (/tmp or ~/.cache) used as download/temp dir on some distros. Needs repro + temp-dir audit of gogdl/legendary invocations |
| #97 / #60 | Import existing installed game files (incl. Heroic installs) | **P2** | High | L | some | `legendary import` and gogdl equivalents exist; UX = "locate existing install" flow. High demand. Merge #60 into #97 |
| #80 | Unify duplicate entries across stores (FR) | **P2** | High | L | some | Explicitly deferred during 0.7 (`cross_source_dedupe.py` scaffold exists; steam_filter removed pending redesign) |
| #154 / #251 | .NET runtime popups (BG3, Grip on GOG) | **P2** | High | M | some | Extend prefix-compat winetricks verbs (dotnet) keyed per game; dup — merge #251 into #154 |
| #374 | Epic install language selection | **P2** | High | M | none | Mirror the GOG language picker using legendary's `--language`; reuse existing picker UI |
| #156 | Respect GOG hidden games | **P2** | High | S–M | some | GOG API exposes hidden flag; filter at `get_library` |
| #322 / #267 / #193 | GTA5 / RDR2 (Rockstar Launcher) | **P2** | Med | L | heavy | The Rockstar-launcher-inside-prefix problem; consolidate into one canonical issue |
| #192 / #282 | Amazon region (.co.uk/.co.jp) login loop + store region | **P2** | Med | M | some | nile marketplace/region config + auth URL region; consolidate |
| #306 | Detect existing Legendary credentials | P3 | High | S–M | none | Import `~/.config/legendary` tokens on first run (auth-handoff half is obsolete — close that part) |
| #149 | GOG Linux-vs-Windows version selector | P3 | High | M | none | Install-time choice; pairs with #374/#156 GOG picker work |
| #72 | Grey-out uninstalled tiles (FR) | P3 | High | S–M | none | Tile-patch infrastructure from 0.7 store badges makes this cheap now |
| #296 | Amazon Luna streaming (FR) | P3 | Med | M | some | Reuse the xCloud Edge-kiosk + controller-layout pattern |
| #136 | itch.io store (FR) | P3 | Med | L | some | Real API exists; large surface (butler). Park in a "store requests" tracking issue |
| #259 | Scan /mnt for storage devices | P3 | High | S | none | Verify against the 0.7 storage module first — may already work; else trivial |
| #220 | Distorted controller-settings icon | P3 | High | S | none | Asset swap |
| #217 | OUTRIDERS: "Steam client logged in without privileges" | P3 | Low | M | heavy | Game demands a launcher environment; niche |
| #47 | Relocate Proton prefixes to SD card / custom storage | **P2** | High | M | none | Shipped for Ubisoft in 0.7 — generalize the same relocation to all stores' prefixes. Big win for 64GB Decks |
| #71 / #29 | GameVault / self-hosted library support (RomM, Drop) | P3 | Med | L | some | Open PR #315 already prototypes a GameVault store — review it as the vehicle for both issues |

## F. Needs info before triage (3, plus cross-references)

| # | Issue | What to ask |
|---|-------|-------------|
| #373 | "Various issues with epic games" | Split into one issue per problem + `launches/<id>.game.log` on 0.7 |
| #185 | "Config issues in prefix files" | Which game, which config; 0.7 prefix rebuild may already cover it |
| #295 | Mongil demands Epic launcher install | Needs game.log; possibly EGL-check bypass candidate |
| #322 | GTA5 (screenshots only) | Ask for logs; then fold into the Rockstar canonical issue (E) |
| #287-adjacent reports of filter confusion | — | Covered by A/#287 fix; ask anyone still confused to re-report on 0.7 |
| #263 | Microsoft half (Quantum Break) | Confirm ownership type (Game Pass vs purchased) — affects xCloud eligibility |

---

## Suggested closing comments

**Fixed in 0.7** — "This is resolved in v0.7.0 by <feature> — see the release notes. Please update and reopen with fresh logs (`~/.local/share/unifideck/launches/`) if you still hit it."

**Obsolete** — "The store sign-in / game-launch stack this was reported against was completely rewritten in v0.7.0. Closing as part of release cleanup — if this still reproduces on 0.7.0, please open a fresh issue with the new per-launch logs."

**Retest** — "We believe v0.7.0 fixes this (<reason>). Could you retest on 0.7.0? We'll close in ~3 weeks if we don't hear back — a comment reopens it any time."

**Housekeeping recommendations**: apply labels while closing (store + type) per `docs/engineering-roadmap.md` item #3; merge the dup clusters noted in section E (#273→#212, #251→#154, #267+#193+#322→one Rockstar issue, #192+#282→one Amazon-region issue).
