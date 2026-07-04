# UNIFIDECK v0.7.0

*(Draft for the GitHub Release — add screenshots of achievements, playtime stats, cloud-save status, and library badges where marked)*

---

Our biggest release yet. Unifideck 0.7 has been rebuilt from the ground up — launches, installs, sign-ins and syncs are faster and far more reliable, and a whole set of long-requested features finally lands: **Achievements, Playtime Tracking, a Cloud Save overhaul and a built-in Self Updater**.

<!-- screenshot: achievements + playtime on game page -->

## New Features

- **Achievements (GOG & Epic)** - Your achievements now unlock and display for GOG and Epic games, right on the game page. GOG achievements unlock in real time while you play.
- **Playtime Tracking** - Unifideck now records playtime for all your games with play stats and streaks — and syncs your sessions back to your GOG and Epic accounts, so your playtime follows you everywhere.
- **Cloud Saves, Rebuilt** - Cloud save support for Epic and GOG got a full overhaul: save locations are detected far more reliably, a status indicator on the game page shows if cloud saves are active, and a manual sync button lets you push/pull on demand. Your local saves are also protected — newer local saves are never overwritten by older cloud copies, and safety backups are kept.
- **Self Updater** - Unifideck now checks for new releases and updates itself with one click and live progress. This is the last time you'll install us by hand :)
- **Latest GE-Proton by Default** - New installs automatically download and use the latest GE-Proton (better video playback and game compatibility out of the box). Prefer something else? Just use Steam's own Compatibility dropdown per game — your choice now sticks properly across launches.
- **Change Executable** - Game launching the wrong thing (config tools, launchers)? You can now pick the correct executable straight from the game's context menu.
- **Store Badges in Your Library** - Library tiles now show which store each game comes from. <!-- screenshot: library grid with badges -->
- **Native Sorting & Filters for Your Games** - Steam's own library sorting and filtering (including review scores) now works with Unifideck games.
- **Custom Install Locations, Better** - A full directory browser with drive selection and folder creation for choosing exactly where games install.
- **Xbox Cloud Controller Support** - xCloud games now get a proper gamepad layout applied automatically in Gaming Mode — no more manual controller fiddling.
- **Steam Collections Are Now Opt-in** - Collections are off by default (they were syncing to your other devices via Steam Cloud). Turn them on in Quick Access → Unifideck settings if you want them; existing setups are migrated cleanly.

## Ubisoft Connect Overhaul

- **Your Full Library, Found** - Games that previously never appeared after sync (key-redeemed and newer titles) are now detected correctly.
- **No More Vanishing Games** - Fixed games "uninstalling themselves" due to Proton/prefix resets on launch. Your Proton choice is respected — no more switching before install.
- **Stay Signed In** - Uninstalling a game no longer signs you out of Ubisoft Connect.
- **Smarter Library** - Duplicate entries and DLC masquerading as games are cleaned up, and titles you already own on Steam are handled properly. Install feedback is clearer throughout.
- **Custom Storage** - Ubisoft game data can now live on your SD card or custom install location.

## GOG Improvements

- **Install Language Auto-Match** - The language picker now pre-selects your language, and your choice is actually applied to the download.
- **DOSBox Games Fixed** - Classic DOSBox titles now launch with the correct game configuration.
- **Reliable Big Installs** - Large installs no longer freeze at 100%, and reinstalling/repairing will never touch your existing game files destructively.
- **First-Launch Setup** - Games are prepared during install, so your cloud saves are there on the very first launch.

## Enhancements

- **Faster, Steadier Game Pages** - Game details load instantly, with game size, accurate "last played" times, and richer metadata.
- **Better Downloads** - Persistent download history, clearer progress and status, launch straight from the download queue, and safer cancellation.
- **Sign-in Reliability** - Store login windows no longer close on their own in Gaming Mode (goodbye vanishing Microsoft QR code), and failed sign-ins recover cleanly.
- **Startup Cleanup** - Orphaned or broken shortcuts left behind by older versions are detected and cleaned automatically.
- **Localization Overhaul** - Substantially improved translations across all 14 supported languages.
- **Beyond the Deck** - Improved support for Bazzite, CachyOS and other SteamOS-like distros, including SD-card detection and per-system Proton handling.

## Fixed

- **Fixed** installed games showing as "not installed" after a sync (GOG especially)
- **Fixed** the Installed tab/filter swallowing thousands of non-Unifideck shortcuts (emulators, other launchers)
- **Fixed** finished installs not registering unless you stayed on the game page
- **Fixed** uninstalled games lingering under Home → Recent Games
- **Fixed** the library needing a re-sync after every sleep/restart
- **Fixed** stuck sync progress bars and repeated "restart Steam" prompts after a restart
- **Fixed** Unifideck's info panel breaking themes on non-Unifideck games
- **Fixed** games launching companion/config tools instead of the game (see Change Executable)
- **Fixed** first launch after install missing cloud saves
- **Fixed** black-screen video playback in games using BINK video (via the GE-Proton default)
- Many, many more launch, install and sync fixes across every store

**Notes**

- After updating, run a **Sync** (Quick Access → Unifideck) and restart Steam once so the new library features (badges, sorting, achievements) fully load.
- Collections are now **opt-in** — enable them in settings if you used them before.
- If a game misbehaved on an older version, please try it again on 0.7.0 before reporting — the launch stack is new, and per-game logs now live in `~/.local/share/unifideck/launches/` if you need to attach one.

Major thanks to @src893 for the enormous engineering effort behind this release, and to all of our community testers on Discord for months of feedback.

Join our [Discord](https://discord.gg/s9KVK2jRnp)!
