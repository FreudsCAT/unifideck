import { call } from "@decky/api";

export type ShortcutLaunchContext = {
  success: boolean;
  store_game_id?: string;
  tool_name?: string;
  appid_unsigned?: number;
  is_linux_runtime?: boolean;
  launcher_path?: string;
  current_launch_options?: string;
  saved_proton_tool?: string;
  error?: string;
};

export type ShortcutLaunchResult = {
  success: boolean;
  already_running?: boolean;
  error?: string;
};

const RESTORE_POLL_DELAY_MS = 250;
const RESTORE_START_DELAY_MS = 500;
const RESTORE_TIMEOUT_MS = 5000;

export function getShortcutRunGameId(appId: number): string {
  const appStore = (window as any).appStore;
  const overview = appStore?.m_mapApps?.get?.(appId);
  return overview?.gameid ?? String(appId);
}

function getShortcutDisplayStatus(appId: number): number | undefined {
  const appStore = (window as any).appStore;
  const overview = appStore?.m_mapApps?.get?.(appId);
  if (!overview) {
    return undefined;
  }

  return (
    overview.local_per_client_data?.display_status ??
    overview.per_client_data?.[0]?.display_status
  );
}

export function isShortcutAppRunning(appId: number): boolean {
  const displayStatus = getShortcutDisplayStatus(appId);
  return displayStatus === 1 || displayStatus === 4;
}

export function terminateShortcutApp(appId: number): boolean {
  try {
    window.SteamClient?.Apps?.TerminateApp?.(getShortcutRunGameId(appId), false);
    return true;
  } catch (error) {
    console.error("[UbisoftShortcutLaunch] TerminateApp failed:", error);
    return false;
  }
}

function scheduleLaunchStateRestore(
  appId: number,
  originalLaunchOptions: string,
  compatToolName: string,
  alreadyRunning: boolean,
): void {
  let restored = false;

  const restore = () => {
    if (restored) {
      return;
    }
    restored = true;

    if (compatToolName) {
      window.SteamClient?.Apps?.SpecifyCompatTool?.(appId, compatToolName);
    }
    window.SteamClient?.Apps?.SetShortcutLaunchOptions?.(
      appId,
      originalLaunchOptions,
    );
  };

  const startedAt = Date.now();
  const poll = () => {
    if (
      (!alreadyRunning && isShortcutAppRunning(appId)) ||
      Date.now() - startedAt >= RESTORE_TIMEOUT_MS
    ) {
      restore();
      return;
    }

    window.setTimeout(poll, RESTORE_POLL_DELAY_MS);
  };

  window.setTimeout(poll, RESTORE_START_DELAY_MS);
}

export async function launchUbisoftInstallViaShortcut(
  storeGameId: string,
): Promise<ShortcutLaunchResult> {
  const context = await call<[string], ShortcutLaunchContext>(
    "get_compat_tool_for_game",
    storeGameId,
  );
  if (!context?.success || !context.appid_unsigned || !context.store_game_id) {
    return {
      success: false,
      error: context?.error || "Shortcut context unavailable",
    };
  }

  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame || !steamApps?.SetShortcutLaunchOptions) {
    return { success: false, error: "Steam launch APIs unavailable" };
  }

  const appId = context.appid_unsigned;
  const originalOptions =
    context.current_launch_options || context.store_game_id;

  // Insert UNIFIDECK_UBISOFT_ACTION=install before #%command%
  // Keeps #%command% intact so shell evaluates KEY=VALUE as env vars
  const installOptions = originalOptions.replace(
    /#%command%/,
    "UNIFIDECK_UBISOFT_ACTION=install #%command%",
  );

  const alreadyRunning = isShortcutAppRunning(appId);
  steamApps.SetShortcutLaunchOptions(appId, installOptions);
  steamApps.RunGame(getShortcutRunGameId(appId), "", -1, 100);

  // Restore original options after launch starts (no compat tool touched)
  scheduleLaunchStateRestore(appId, originalOptions, "", alreadyRunning);

  return { success: true, already_running: alreadyRunning };
}

export async function launchUbisoftAuthViaShortcut(): Promise<ShortcutLaunchResult> {
  // Get the auth shortcut's appid from the backend registry.
  const context = await call<
    [],
    { success: boolean; appid_unsigned?: number; error?: string }
  >("get_ubisoft_auth_shortcut_context");
  if (!context?.success || !context.appid_unsigned) {
    return {
      success: false,
      error: context?.error || "Auth shortcut not available",
    };
  }

  // Start session monitor (captures UPC credentials after user logs in)
  call<[], { success: boolean }>("start_ubisoft_auth_session_monitor").catch(
    () => {},
  );

  // Launch directly via RunGame — identical to clicking "Play".
  // The permanent VDF launch options already have #%command% (skip compat tool)
  // and the UNIFIDECK_* env vars baked in. No temp options needed.
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame) {
    return { success: false, error: "Steam launch API unavailable" };
  }

  steamApps.RunGame(
    getShortcutRunGameId(context.appid_unsigned),
    "",
    -1,
    100,
  );
  return { success: true };
}
