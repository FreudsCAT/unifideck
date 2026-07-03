/**
 * useLaunchPrep — Force-Compatibility prep for installed games.
 *
 * Ported from staging's PlayButtonOverride page-load effect.
 *
 * The launcher (``bin/unifideck-launcher``) applies Proton itself
 * via umu. If Steam's "Force Compatibility" is set on the shortcut,
 * ``RunGame`` would *also* wrap our launcher in Proton — producing
 * the perpetual loading screen in Gaming Mode (and a double-Proton
 * session). So, while a game-details page is open, we:
 *
 *   1. Read the shortcut's current Force-Compat tool from the
 *      backend (``get_compat_tool_for_game``).
 *   2. If a real Proton tool is set, persist it to
 *      ``proton_settings.json`` (``save_proton_setting``) so the
 *      launcher re-applies the user's choice, then clear Force
 *      Compatibility so ``RunGame`` runs the launcher natively.
 *   3. On page-leave, restore the user's Force-Compat selection so
 *      it still shows in Steam's Properties dialog. It gets cleared
 *      again on the next page load (the effect re-runs).
 *
 * Steam-Linux-Runtime entries are not real Proton — they're left
 * untouched. xCloud (browser-streamed) titles never use Proton.
 */
import { useEffect, useRef } from "react";
import { call } from "@decky/api";
import { rpcRoutes } from "../api/rpc-routes";
import type { Game } from "../types/api";
import type { ShortcutLaunchContext } from "../lib/steam-bridge/shortcut-types";

/** Non-Steam shortcut appids live above this Steam reserved range. */
const NON_STEAM_APPID_FLOOR = 2_000_000_000;

interface ClearedFC {
  toolName: string;
}

/**
 * Run Force-Compatibility prep while an installed Unifideck game's
 * details page is mounted. No-op for Steam-native games, uninstalled
 * games, and xCloud titles.
 */
export function useLaunchPrep(
  appId: number,
  game: Game | null | undefined,
): void {
  const clearedRef = useRef<ClearedFC | null>(null);

  const store = game?.store;
  const gameId = game?.id;
  const installed = game?.is_installed ?? false;
  const isXcloud = game?.store_tags?.includes("xcloud") ?? false;

  useEffect(() => {
    if (appId <= NON_STEAM_APPID_FLOOR) return;
    if (!store || !gameId || !installed || isXcloud) return;

    let cancelled = false;
    const storeGameId = `${store}:${gameId}`;
    const steamApps = window.SteamClient?.Apps;

    (async () => {
      try {
        const ctx = await call<[string], ShortcutLaunchContext>(
          rpcRoutes.getCompatToolForGame,
          storeGameId,
        );
        if (cancelled || !ctx?.success) return;

        const toolName = ctx.tool_name ?? "";
        // Only act on a real Proton tool — Steam-Linux-Runtime
        // entries are runtimes, not compat tools, and clearing them
        // would break native titles that legitimately use one.
        if (toolName && !ctx.is_linux_runtime) {
          // Save the tool for the launcher to re-apply ONLY when it's a
          // genuine per-game choice. A tool equal to Steam's global
          // default (`is_global_default`) is a distro/system default —
          // e.g. Bazzite's "Proton-CachyOS Latest" applied to every
          // shortcut. Adopting it made the launcher try to resolve an
          // unavailable tool, silently fall back to GE-Proton, and thrash
          // the prefix ("Resetting Proton"). So for the global default we
          // instead clear any stale saved override (self-heals users a
          // prior build already broke), letting the launcher use its GE
          // default. Either way we clear Steam's Force-Compat so RunGame
          // doesn't double-wrap the launcher (perpetual loading screen).
          const saveTool = ctx.is_global_default ? "" : toolName;
          await call<[string, string], { success: boolean }>(
            rpcRoutes.saveProtonSetting,
            storeGameId,
            saveTool,
          );
          steamApps?.SpecifyCompatTool?.(appId, "");
          clearedRef.current = { toolName };
          console.log(
            `[useLaunchPrep] ${
              ctx.is_global_default
                ? `cleared saved override (global default ${toolName})`
                : `saved (${toolName})`
            } + cleared Force-Compat for app ${appId}`,
          );
        }
      } catch (e) {
        console.error("[useLaunchPrep] compat-tool prep failed:", e);
      }
    })();

    return () => {
      cancelled = true;
      const cleared = clearedRef.current;
      if (cleared) {
        steamApps?.SpecifyCompatTool?.(appId, cleared.toolName);
        clearedRef.current = null;
        console.log(
          `[useLaunchPrep] restored Force-Compat (${cleared.toolName}) for app ${appId}`,
        );
      }
    };
  }, [appId, store, gameId, installed, isXcloud]);
}
