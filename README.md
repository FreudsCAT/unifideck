# Unifideck - Unified Game Library for Steam Deck

A Decky Loader plugin that brings together Steam, Epic Games Store, GOG, Amazon Games, Ubisoft Connect, and Xbox Cloud Gaming in a single library experience on your Steam Deck.

![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)
![Platform](https://img.shields.io/badge/platform-Steam%20OS-orange.svg)
![Downloads](https://img.shields.io/github/downloads/mubaraknumann/unifideck/total.svg?label=downloads&color=brightgreen)
[![Sponsor](https://img.shields.io/badge/Sponsor-GitHub-ea4aaa?logo=github&logoColor=white)](https://github.com/sponsors/mubaraknumann) [![Ko-fi](https://img.shields.io/badge/Ko--fi-F16061?logo=ko-fi&logoColor=white)](https://ko-fi.com/mubaraknumann)

## Table of Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Getting Started](#getting-started)
- [Documentation](#documentation)
- [Known Limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Languages](#languages)
- [Building](#building)
- [Tech Stack](#tech-stack)
- [Credits](#credits)
- [Support](#support)
- [License](#license)
- [Author](#author)
- [Disclaimer](#disclaimer)

## Features

- **Unified library tabs** - Browse Steam, Epic, GOG, Amazon, Ubisoft, Xbox Cloud Gaming, Installed, Great on Deck, and Non-Steam from one place.
- **Steam-native install, update, and launch actions** - Manage supported games directly from the game details view, with progress and status feedback.
- **Shortcut-based sign-in in Gaming Mode** - Authenticate Epic, GOG, Amazon, Ubisoft, and Microsoft without leaving the Steam UI.
- **Flexible install locations** - Use internal storage, SD card, or a validated custom install directory.
- **Launch options and Proton control** - Preserve custom launch options across syncs, installs, and Proton toggles. Supports wrappers, MangoHud, LSFG, `PROTON=`, and `PROTONPATH=`.
- **Artwork and richer metadata** - Pull cover art, icons, banners, store links, Metacritic data, and Great on Deck style compatibility info where available.
- **Cloud saves** - Epic and GOG cloud saves are supported, including conflict prompts when both local and cloud saves exist.
- **Store-specific extras** - GOG language selection, Epic/GOG DLC auto-downloads, Epic offline mode, GOG Galaxy / Comet support for compatible titles, and xCloud "Play on Cloud" support through Edge.

## Screenshots

### Unified Game Library

<img width="1920" height="1080" alt="Screenshot_20260109_123258" src="https://github.com/user-attachments/assets/58aafad6-5c54-475d-a309-c44f77895b72" />

### Game Details

![20260104022821_1](https://github.com/user-attachments/assets/afc0922e-aace-4d47-925e-1bc7f1e48140)

## Prerequisites

- **Decky Loader** must be installed on your Steam Deck.
- **Microsoft Edge** is required for store sign-in and Xbox Cloud Gaming. If it is missing, Unifideck will prompt you to install it.
- All other store CLIs and helper tooling are bundled with the plugin.

[Decky Loader Installation Guide](https://github.com/SteamDeckHomebrew/decky-loader)

## Installation

1. Download the latest plugin ZIP from the [Releases](https://github.com/mubaraknumann/unifideck/releases) page.
2. Open **Quick Access Menu** (three dots button).
3. Navigate to **Decky** -> **Settings** (gear icon).
4. Enable **Developer Mode** if it is not already enabled.
5. Click **Install Plugin from ZIP**.
6. Select the downloaded ZIP file.

If an update gets stuck on `installing plugin`, uninstall the current Unifideck plugin and install the latest ZIP again.

https://www.youtube.com/watch?v=lP-90uYd72w

## Getting Started

1. Open the **Quick Access Menu** and launch **Unifideck**.
2. Connect the stores you want to use.
3. Set your default install location if you want internal storage, SD card, or a custom path.
4. Run **Sync Libraries** or **Force Sync**.
5. Restart Steam when prompted so new shortcuts and artwork are applied.
6. For Ubisoft titles purchased through Epic, complete the one-time account link at [epicgames.com/id/link/ubisoft](https://epicgames.com/id/link/ubisoft).

Installed games are playable immediately after install. The Steam restart is still needed after sync or cleanup so the library refreshes fully.

## Documentation

- **[FAQ](docs/faq.md)** - Common issues, workarounds, and version-specific fixes collected from releases, code comments, and GitHub issues.
- **[Launch Options Guide](docs/launch-options.md)** - Custom parameters, wrappers, LSFG, and per-game launch tweaks.
- **[Proton Compatibility Notes](docs/proton-compatibility.md)** - `PROTON=`, `PROTONPATH=`, and compatibility troubleshooting.

## Known Limitations

- Unifideck replaces Steam's default **All Games**, **Installed**, and **Great on Deck** tabs, so Steam's standard sort and filter behavior is not preserved there.
- With **TabMaster** installed, Unifideck skips custom tab injection and relies on `[Unifideck]` collections instead.
- Steam still needs a restart after sync or cleanup so new shortcuts and artwork fully apply.
- Xbox Cloud Gaming support is **streaming-only** and depends on **Microsoft Edge**.
- Cloud saves currently cover **Epic** and **GOG** only, and game-level support varies.
- Some titles still need manual Proton experimentation or store-specific workarounds.
- Not every game has SteamGridDB artwork or complete metadata.
- For **Ubisoft**, choose your Proton version **before** installing. Changing Proton after install can invalidate the prefix and force a reinstall.

## Troubleshooting

For a longer list of release-specific problems and fixes, see the **[FAQ](docs/faq.md)**.

### Install Stuck on `installing plugin`

Uninstall the current plugin and install the latest ZIP again. This was the recommended workaround for the 0.6.0 -> 0.6.1 transition.

### Games or Artwork Do Not Appear After Sync

Run **Force Sync** if needed, then restart Steam when prompted so shortcuts and artwork are reloaded.

### Epic Login Shows a Blank Page or `Pretty Print`

Sign into Epic in a regular browser first, accept any pending legal updates, then retry in Unifideck.

### A Game Will Not Install or Launch

Check available storage, make sure the store account is still connected, and inspect `~/.local/share/unifideck/launcher.log`.

### Microsoft / xCloud Will Not Open

Install Microsoft Edge when prompted. After the first successful Microsoft sign-in, you may still need to click **Play via Cloud** once inside the xCloud home screen to finish OAuth.

### Ubisoft Titles from Epic Hang on Login or Ask for a Key

Make sure your Epic and Ubisoft accounts are linked at [epicgames.com/id/link/ubisoft](https://epicgames.com/id/link/ubisoft). If problems continue, clear `~/.local/share/unifideck/chromium-auth`, `~/.local/share/unifideck/ubisoft_installer_cache`, and the Ubisoft prefixes under `~/.local/share/unifideck/prefixes/`, then try again.

### Logs

- **Decky/backend log** - `/home/deck/homebrew/logs/Unifideck`
- **Launcher/runtime log** - `~/.local/share/unifideck/launcher.log`
- **Edge/browser log** - `~/.local/share/unifideck/chromium-auth.log`

## Languages

Unifideck currently ships with English (US), French, Brazilian Portuguese, Russian, Japanese, German, Spanish, Italian, Simplified Chinese, Korean, Dutch, Polish, Turkish, and Ukrainian.

To add a new language, create a JSON file in `src/i18n/locales/` using `en-US.json` as the template and wire it into the language selector.

## Building

To build the plugin from source:

1. Install dependencies: `pnpm install`
2. Build the frontend bundle: `pnpm run build`
3. Build the plugin package:
   - standard Decky / fork workflow: `./.vscode/build.sh` or the VS Code `build-plugin` task

For frontend watch mode, use `pnpm run watch`.

## Tech Stack

- **Frontend** - React, TypeScript, Rollup, `@decky/api`, `@decky/ui`, `i18next`
- **Backend** - Python, Decky Loader RPC, CDP-based auth and browser helpers
- **Store tooling** - legendary, gogdl, nile, comet, winetricks, umu-launcher
- **Services and data** - SteamGridDB, Epic/GOG/Amazon/Microsoft APIs, Microsoft Edge, Metacritic, compatibility metadata

## Credits

This project builds on a lot of open source work and community help.

- **Platform and UI** - [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader), `@decky/api`, `@decky/ui`, and the SteamDeckHomebrew community
- **Store and runtime tooling** - [legendary](https://github.com/derrod/legendary), gogdl, [nile](https://github.com/imLinguin/nile), [comet](https://github.com/imLinguin/comet), [winetricks](https://github.com/Winetricks/winetricks), [umu-launcher](https://github.com/Open-Wine-Components/umu-launcher), and [SteamGridDB](https://www.steamgriddb.com/)
- **Reference projects and patterns** - [TabMaster](https://github.com/CEbbinghaus/TabMaster), [SteamGridDB Decky](https://github.com/SteamGridDB/decky-steamgriddb), [ProtonDB Decky](https://github.com/OMGDuke/protondb-decky), [Heroic Games Launcher](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher), and [Junk-Store](https://github.com/ebenbruyns/junkstore)
- **Special thanks** - @src893, @xXJSONDeruloXx, @moi952, @Lazer-zx5, @buddax2, @Grails125, DeckWizard, u/EnTei7K, u/IN50MNIAC, derrod, and the Discord testers for invaluable feedback.

## Support

If you want to support development or keep up with releases:

- [Become a GitHub Sponsor](https://github.com/sponsors/mubaraknumann)
- [Buy me a coffee on Ko-fi](https://ko-fi.com/mubaraknumann)
- [Join the Discord](https://discord.gg/s9KVK2jRnp)

## License

GNU General Public License v3.0 or later - see [LICENSE](./LICENSE) for details.

## Author

Numan Mubarak (numanmuabrak@protonmail.com)

## Disclaimer

This is an unofficial third-party tool. It is not affiliated with Valve, Epic Games, CD Projekt / GOG, Amazon, Ubisoft, or Microsoft.
