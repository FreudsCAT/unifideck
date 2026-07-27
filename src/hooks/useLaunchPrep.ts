/**
 * useLaunchPrep — Force-Compatibility prep for installed games.
 *
 * Ported from staging's PlayButtonOverride page-load effect.
 *
 * The launcher (``bin/unifideck-launcher``) applies Proton itself
 * via umu. If Steam's "Force Compatibility" is set on the shortcut,
 * ``RunGame`` would *also* wrap our launcher in Proton — producing
 * the perpetual loading screen in Gaming Mode (and a double-Proton
 * session). So:
 *
 *   1. Read the shortcut's current Force-Compat tool from the
 *      backend (``get_compat_tool_for_game``).
 *   2. If a real Proton tool is set, persist it to
 *      ``proton_settings.json`` (``save_proton_setting``) so the
 *      launcher re-applies the user's choice, then clear Force
 *      Compatibility so ``RunGame`` runs the launcher natively.
 *   3. On page-leave, restore the user's Force-Compat selection so
 *      it still shows in Steam's Properties dialog.
 *
 * This sync is NOT only run once on page mount — it is exposed as
 * ``syncBeforeLaunch`` and MUST be awaited right before every
 * ``RunGame`` call. Steam's Properties > Compatibility dialog is an
 * overlay, not a page navigation: a user can reopen it and pick a
 * *different* Proton version any number of times while staying on
 * the same game-details page, and none of that ever remounts this
 * hook's effect. Relying on mount-time-only capture meant every
 * Force-Compat change after the first page load was never read or
 * cleared, so Steam nest-wrapped the launcher in whatever the user
 * had most recently picked — reproduced live: umu-run exits 127
 * ("libz.so.1" missing) regardless of which Proton was selected,
 * because the nesting itself is the failure, not the specific build.
 * The whole point of reading Steam's native picker is to let the
 * user freely try different Proton versions and have the launcher
 * apply whichever one they land on — so this needs to work on every
 * Play press, not just the first one after opening the page.
 *
 * Steam-Linux-Runtime entries are not real Proton — they're left
 * untouched. xCloud (browser-streamed) titles never use Proton.
 */
import { useCallback, useEffect, useRef } from "react";
import { call } from "@decky/api";
import { rpcRoutes } from "../api/rpc-routes";
import type { Game } from "../types/api";
import type { ShortcutLaunchContext } from "../lib/steam-bridge/shortcut-types";

/** Non-Steam shortcut appids live above this Steam reserved range. */
const NON_STEAM_APPID_FLOOR = 2_000_000_000;

interface ClearedFC {
  toolName: string;
}

interface CompatToolSyncResult {
  /** True if a real, non-linux-runtime Force-Compat override was live
   * and has now been cleared from Steam's side. */
  cleared: boolean;
  /** The tool name that was live (meaningful only when ``cleared``). */
  toolName: string;
}

/**
 * Read Steam's current Force-Compat selection for ``storeGameId``,
 * persist a genuine per-game choice to ``proton_settings.json`` (so the
 * launcher re-applies it internally via umu), and clear Steam's own
 * override so ``RunGame`` runs the launcher natively instead of
 * double-wrapping it.
 *
 * A tool equal to Steam's global default (``is_global_default``) is a
 * distro/system default (e.g. Bazzite's "Proton-CachyOS Latest") applied
 * to every shortcut, not a genuine per-game choice — adopting it made the
 * launcher try to resolve an unavailable tool and thrash the prefix. For
 * that case only the SAVE is skipped; Steam's per-app entry is still
 * cleared regardless, because Steam nest-wraps on the mere presence of a
 * per-app entry, not on whether its value differs from the global default.
 *
 * Plain (non-hook) function so it can be called from any Play entry
 * point, not just the game-details page — e.g. the downloads tab's own
 * Play button, which never mounts ``useLaunchPrep`` at all.
 */
export async function syncCompatToolBeforeLaunch(
  appId: number,
  storeGameId: string,
): Promise<CompatToolSyncResult> {
  if (appId <= NON_STEAM_APPID_FLOOR) return { cleared: false, toolName: "" };
  const steamApps = window.SteamClient?.Apps;
  try {
    const ctx = await call<[string], ShortcutLaunchContext>(
      rpcRoutes.getCompatToolForGame,
      storeGameId,
    );
    if (!ctx?.success) return { cleared: false, toolName: "" };

    const toolName = ctx.tool_name ?? "";
    // Only act on a real Proton tool — Steam-Linux-Runtime entries are
    // runtimes, not compat tools, and clearing them would break native
    // titles that legitimately use one.
    if (!toolName || ctx.is_linux_runtime) {
      return { cleared: false, toolName: "" };
    }

    const saveTool = ctx.is_global_default ? "" : toolName;
    await call<[string, string], { success: boolean }>(
      rpcRoutes.saveProtonSetting,
      storeGameId,
      saveTool,
    );
    steamApps?.SpecifyCompatTool?.(appId, "");
    console.log(
      `[useLaunchPrep] ${
        ctx.is_global_default
          ? `cleared saved override (global default ${toolName})`
          : `saved (${toolName})`
      } + cleared Force-Compat for app ${appId}`,
    );
    return { cleared: true, toolName };
  } catch (e) {
    console.error("[useLaunchPrep] compat-tool sync failed:", e);
    return { cleared: false, toolName: "" };
  }
}

/**
 * Run Force-Compatibility prep for an installed Unifideck game's
 * details page. No-op for Steam-native games, uninstalled games, and
 * xCloud titles.
 *
 * Returns ``syncBeforeLaunch`` — callers MUST await this immediately
 * before every ``RunGame``/``actions.launch`` call (not just rely on
 * the mount-time effect below), since Steam's own Properties dialog can
 * change the live Force-Compat selection at any time without this page
 * remounting.
 */
export function useLaunchPrep(
  appId: number,
  game: Game | null | undefined,
): { syncBeforeLaunch: () => Promise<void> } {
  const clearedRef = useRef<ClearedFC | null>(null);

  const store = game?.store;
  const gameId = game?.id;
  const installed = game?.is_installed ?? false;
  const isXcloud = game?.store_tags?.includes("xcloud") ?? false;

  const syncBeforeLaunch = useCallback(async (): Promise<void> => {
    if (!store || !gameId || !installed || isXcloud) return;
    const result = await syncCompatToolBeforeLaunch(
      appId,
      `${store}:${gameId}`,
    );
    if (result.cleared) clearedRef.current = { toolName: result.toolName };
  }, [appId, store, gameId, installed, isXcloud]);

  useEffect(() => {
    if (!store || !gameId || !installed || isXcloud) return;
    let cancelled = false;

    (async () => {
      const result = await syncCompatToolBeforeLaunch(
        appId,
        `${store}:${gameId}`,
      );
      if (!cancelled && result.cleared) {
        clearedRef.current = { toolName: result.toolName };
      }
    })();

    return () => {
      cancelled = true;
      const cleared = clearedRef.current;
      if (cleared) {
        window.SteamClient?.Apps?.SpecifyCompatTool?.(appId, cleared.toolName);
        clearedRef.current = null;
        console.log(
          `[useLaunchPrep] restored Force-Compat (${cleared.toolName}) for app ${appId}`,
        );
      }
    };
  }, [appId, store, gameId, installed, isXcloud]);

  return { syncBeforeLaunch };
}
