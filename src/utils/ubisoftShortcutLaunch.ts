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

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function extractUserParams(
  launchOptions: string,
  storeGameId: string,
  launcherPath?: string,
): string {
  let cleaned = launchOptions.replace(/\s*#%command%\s*$/g, "");
  const escapedStoreGameId = escapeRegExp(storeGameId);

  // Remove UNIFIDECK_* env vars FIRST so KEY=storeGameId is removed as a unit
  // (prevents leaving broken "UNIFIDECK_AUTH=" when storeGameId is stripped later)
  cleaned = cleaned.replace(/\bUNIFIDECK_[A-Z0-9_]+=(?:"[^"]*"|\S+)/g, "");

  // Then remove standalone storeGameId tokens (not inside KEY=value pairs)
  cleaned = cleaned
    .replace(new RegExp(`"${escapedStoreGameId}"`, "g"), "")
    .replace(new RegExp(`(?<=^|\\s)${escapedStoreGameId}(?=\\s|$)`, "g"), "");

  if (launcherPath) {
    const escapedLauncherPath = escapeRegExp(launcherPath);
    cleaned = cleaned
      .replace(new RegExp(`"${escapedLauncherPath}"`, "g"), "")
      .replace(new RegExp(escapedLauncherPath, "g"), "");
  }

  return cleaned.replace(/\s{2,}/g, " ").trim();
}

function buildTemporaryLaunchOptions(
  context: ShortcutLaunchContext,
  extraEnv: Record<string, string>,
  launchStoreGameId?: string,
): string {
  const sourceStoreGameId = context.store_game_id ?? "";
  const storeGameId = launchStoreGameId ?? sourceStoreGameId;
  const currentOptions = context.current_launch_options ?? sourceStoreGameId;
  const userParams = extractUserParams(
    currentOptions,
    sourceStoreGameId,
    context.launcher_path,
  );
  const envTokens = Object.entries(extraEnv)
    .filter(([, value]) => Boolean(value))
    .map(([key, value]) => `${key}=${value}`)
    .join(" ");

  return [storeGameId, envTokens, userParams].filter(Boolean).join(" ").trim();
}

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

async function launchShortcutWithTemporaryOptions(
  context: ShortcutLaunchContext,
  extraEnv: Record<string, string>,
  launchStoreGameId?: string,
): Promise<ShortcutLaunchResult> {
  if (!context?.success) {
    return {
      success: false,
      error: context?.error || "Shortcut launch context unavailable",
    };
  }

  const appId = context.appid_unsigned;
  const storeGameId = context.store_game_id;
  if (typeof appId !== "number" || !storeGameId) {
    return {
      success: false,
      error: "Shortcut launch context is incomplete",
    };
  }

  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame || !steamApps?.SetShortcutLaunchOptions) {
    return {
      success: false,
      error: "Steam shortcut launch APIs are unavailable",
    };
  }

  const alreadyRunning = isShortcutAppRunning(appId);
  const originalLaunchOptions = context.current_launch_options ?? storeGameId;
  const temporaryLaunchOptions = buildTemporaryLaunchOptions(
    context,
    extraEnv,
    launchStoreGameId,
  );
  const compatToolName =
    context.tool_name && !context.is_linux_runtime ? context.tool_name : "";

  try {
    if (compatToolName && context.saved_proton_tool !== compatToolName) {
      await call<[string, string], { success: boolean }>(
        "save_proton_setting",
        storeGameId,
        compatToolName,
      );
    }

    if (compatToolName) {
      steamApps.SpecifyCompatTool?.(appId, "");
    }

    steamApps.SetShortcutLaunchOptions(appId, temporaryLaunchOptions);
    steamApps.RunGame(getShortcutRunGameId(appId), "", -1, 100);
    scheduleLaunchStateRestore(
      appId,
      originalLaunchOptions,
      compatToolName,
      alreadyRunning,
    );

    return { success: true, already_running: alreadyRunning };
  } catch (error) {
    console.error("[UbisoftShortcutLaunch] Shortcut launch failed:", error);

    if (compatToolName) {
      steamApps.SpecifyCompatTool?.(appId, compatToolName);
    }
    steamApps.SetShortcutLaunchOptions?.(appId, originalLaunchOptions);

    return {
      success: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

export async function launchUbisoftInstallViaShortcut(
  storeGameId: string,
): Promise<ShortcutLaunchResult> {
  const context = await call<[string], ShortcutLaunchContext>(
    "get_compat_tool_for_game",
    storeGameId,
  );
  return launchShortcutWithTemporaryOptions(context, {
    UNIFIDECK_UBISOFT_ACTION: "install",
  });
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
