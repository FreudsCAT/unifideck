# Unifideck FAQ

This FAQ keeps to practical issues that have already shown up in release notes, code comments, or GitHub issues and comments. If you are troubleshooting an older install, update to the latest release first.

## Installation and setup

| Issue | Resolution | Source |
| --- | --- | --- |
| The plugin install stalls around `installing plugin`. | Uninstall the current Unifideck plugin, then install the latest ZIP again. This was the recommended workaround during the 0.6.0 -> 0.6.1 transition. | Release-0.6.1; issue [#262](https://github.com/mubaraknumann/unifideck/issues/262) |
| Unifideck says a browser is required before sign-in or xCloud can work. | Install Microsoft Edge when prompted. Edge is the supported browser for shortcut-based auth and xCloud. | Release-0.6.1; `src/index.tsx`; `py_modules/unifideck/stores/microsoft_chromium.py` |
| I want installs somewhere other than internal storage. | Use **Storage Settings** to choose internal storage, SD card, or a validated custom path. | Release-0.6.0; `src/components/StorageSettings.tsx`; `py_modules/unifideck/download/manager.py` |
| My new games are synced but do not appear in Steam yet. | Restart Steam when Unifideck prompts you. Sync and cleanup still need a Steam restart to fully refresh shortcuts and artwork. | `src/components/SteamRestartModal.tsx` comment |
| I use multiple Steam accounts and old data is still hanging around. | Use the account-switch prompt to migrate existing data or clear it cleanly instead of manually deleting shortcuts. | Release-0.5.4 |

## Authentication and account connection

| Issue | Resolution | Source |
| --- | --- | --- |
| Epic login opens but the final page is blank or shows `Pretty Print`. | Sign into Epic in a normal browser first, accept any pending legal updates, then retry in Unifideck. | Issue [#137](https://github.com/mubaraknumann/unifideck/issues/137) comments |
| Microsoft sign-in completes but xCloud still does not start cleanly on the first try. | After the first successful sign-in, open xCloud once and click **Play via Cloud** inside the Microsoft Cloud Gaming home screen to finish OAuth. | Release-0.6.1 |
| Microsoft or Ubisoft auth times out unexpectedly. | A full SteamOS reboot is a good first retry. One issue thread also reported better Ubisoft auth behavior with GE-Proton-10-23 than Proton Hotfix or Experimental. | Issue [#260](https://github.com/mubaraknumann/unifideck/issues/260) comments |
| Ubisoft games bought through Epic hang on login or ask for a key. | Link your Epic and Ubisoft accounts once at `epicgames.com/id/link/ubisoft`, then retry the launch. | Release-0.5.5; release-0.6.1 |
| Sign-in loops or keeps failing after updates, especially with Ubisoft. | Clear `~/.local/share/unifideck/chromium-auth`, `~/.local/share/unifideck/ubisoft_installer_cache`, and the Ubisoft prefixes under `~/.local/share/unifideck/prefixes/`, then try again. | Release-0.6.1 |

## Library sync, artwork, and display

| Issue | Resolution | Source |
| --- | --- | --- |
| All artwork is missing after sync. | Run **Force Sync** from Library Sync and restart Steam. That resolved issue #222, and later releases also shipped artwork sync fixes. | Issue [#222](https://github.com/mubaraknumann/unifideck/issues/222) comments; release-0.5.3 |
| Cover art disappeared after an older 0.5.x build. | Update to the latest release and force-sync artwork again. The artwork sync path was fixed in 0.5.3 and the query/filtering was improved again in 0.6.0. | Release-0.5.3; release-0.6.0; issue [#252](https://github.com/mubaraknumann/unifideck/issues/252) |
| The custom Install / Play area is too low on the page or I see duplicate play buttons. | Update to at least 0.5.5. The public fix covered the language-sensitive native play button hiding logic and game-details placement issues. | Issue [#219](https://github.com/mubaraknumann/unifideck/issues/219); release-0.5.5 |
| Great on Deck / richer metadata does not show up right away. | Sync or force-sync once, then restart Steam so the richer metadata can be loaded into the library view. | Release-0.5.2 |
| All libraries show `0` games even after a sync. | Update to at least 0.4.2 and sync again. That release explicitly fixed the all-zero library state for affected users. | Release-0.4.2 |
| TabMaster is installed and I do not see Unifideck's custom tabs. | This is expected. Unifideck skips custom tab injection when TabMaster is present and expects you to use `[Unifideck]` collections instead. | `src/tabs/LibraryPatch.ts` comments |

## Downloads, updates, and launch behavior

| Issue | Resolution | Source |
| --- | --- | --- |
| Launch options keep resetting after a reboot. | As a temporary workaround, append your extra options to the end of the **Target** field instead. | Issue [#273](https://github.com/mubaraknumann/unifideck/issues/273) comment |
| LSFG does not activate. | Use `~/lsfg` or `LSFG=1`, and make sure the required LSFG pieces are actually installed and configured. The LSFG path was specifically fixed in 0.6.0. | Release-0.6.0; issue [#236](https://github.com/mubaraknumann/unifideck/issues/236) |
| Ubisoft games uninstall themselves after I change Proton. | Choose the Proton version before installing. Changing Proton after install can invalidate the prefix and force a redownload. | Release-0.6.1; issue [#272](https://github.com/mubaraknumann/unifideck/issues/272) |
| A GOG DOSBox game launches the wrong executable. | Update to 0.6.0 or newer. Generic DOSBox fixes were added there after reports like issue #248. | Release-0.6.0; issue [#248](https://github.com/mubaraknumann/unifideck/issues/248) |
| Epic or GOG DLC is missing after install. | Use a build from 0.6.0 or newer and rerun the install or update path. Owned DLCs are downloaded automatically there. | Release-0.6.0; `py_modules/unifideck/download/manager.py`; `py_modules/unifideck/dlc.py` |
| The download / update progress UI is missing from the game details page. | Update to at least 0.5.5. That release added the progress tracker and update-status integration to the custom play section. | Release-0.5.5 |
| A GOG game stopped launching after an update. | Update to at least 0.5.6. That release specifically fixed GOG launch regressions. | Release-0.5.6 |
| Large GOG downloads cancel or never finish properly on old builds. | Update to at least 0.4.0, which fixed large multi-part GOG downloads being canceled before completion. | Release-0.4.0 |
| Game updates are not detected consistently. | Update to at least 0.5.5. Later builds added explicit update checks and update progress wiring for the play section. | Release-0.5.5; `src/components/PlayButtonOverride.tsx`; `py_modules/unifideck/download/update_checker.py` |

## Store-specific behavior

| Issue | Resolution | Source |
| --- | --- | --- |
| A GOG game never asked me for a language. | The language modal only appears for games that actually expose multiple supported languages. The feature was added in 0.5.4. | Release-0.5.4; `src/components/GOGLanguageSelectModal.tsx` |
| Epic titles that need Ubisoft / Uplay prerequisites still fail. | Update to at least 0.5.5 and make sure the Epic and Ubisoft accounts are linked. That release added the automatic prerequisite / login path for Epic games that depend on Ubisoft Connect. | Release-0.5.5 |
| Online GOG titles such as Gwent do not connect through GOG Galaxy features. | Update to at least 0.5.2, which added Comet / GOG Galaxy support for compatible titles. | Release-0.5.2 |
| Hidden games keep reappearing in older builds. | Update to at least 0.5.0. Hidden games were explicitly added there and hidden Steam games were kept hidden as well. | Release-0.5.0 |
| Amazon support is missing entirely. | Amazon support first shipped in 0.4.0. If you are on an older package, update. | Release-0.4.0 |
| Cloud saves do not work for every store or every game. | That is expected. Cloud save support currently targets Epic and GOG, and support still depends on the individual game. | Release-0.4.0; `py_modules/unifideck/cloud/cloud_save.py` |

## Logs and debugging

| Issue | Resolution | Source |
| --- | --- | --- |
| I need the main plugin / backend log. | Start with `/home/deck/homebrew/logs/Unifideck`. | Issue [#260](https://github.com/mubaraknumann/unifideck/issues/260) comments; issue [#262](https://github.com/mubaraknumann/unifideck/issues/262) comments |
| I need launcher or xCloud-specific logs. | Use `~/.local/share/unifideck/launcher.log` for launches and installs, and `~/.local/share/unifideck/chromium-auth.log` for Edge / xCloud auth behavior. | `py_modules/unifideck/stores/microsoft_chromium.py`; `py_modules/unifideck/accounts/account_manager.py` |
