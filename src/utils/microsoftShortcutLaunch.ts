/**
 * Launch Microsoft auth Chromium via Steam's RunGame API.
 *
 * This mirrors the Ubisoft auth shortcut pattern: a hidden non-Steam
 * shortcut runs unifideck-launcher with UNIFIDECK_MICROSOFT_ACTION=auth,
 * which launches Chromium with the OAuth URL.  Because Steam manages the
 * game session, gamescope surfaces the Chromium window in gaming mode.
 */
import { call } from "@decky/api";
import {
  type ShortcutLaunchContext,
  type ShortcutLaunchResult,
  getShortcutRunGameId,
  isShortcutAppRunning,
} from "./ubisoftShortcutLaunch";

const MS_AUTH_SHORTCUT_STORE_ID = "microsoft:ms-auth";
const TEMP_MS_AUTH_SHORTCUT_STORE_ID_PREFIX = "microsoft:ms-auth-temp";

const SHORTCUT_POLL_DELAY_MS = 250;
const SHORTCUT_POLL_TIMEOUT_MS = 5000;
const TEMP_SHORTCUT_CLEANUP_DELAY_MS = 15000;
const TEMP_SHORTCUT_POST_REMOVE_REPAIR_DELAY_MS = 2000;

export type MicrosoftShortcutLaunchResult = ShortcutLaunchResult & {
  appId?: number;
};

/** Check if a specific appId is registered in Steam's in-memory app store. */
function isShortcutRegistered(appId: number): boolean {
  const appStore = (window as any).appStore;
  return Boolean(appStore?.m_mapApps?.get?.(appId));
}

function getShortcutLaunchOptionsValue(appId: number): string | null {
  const appStore = (window as any).appStore;
  const app = appStore?.m_mapApps?.get?.(appId);
  const launchOptions =
    app?.launch_options ??
    app?.strLaunchOptions ??
    app?.m_strLaunchOptions ??
    null;
  return typeof launchOptions === "string" ? launchOptions : null;
}

function getShortcutGameId(appId: number): string | null {
  const appStore = (window as any).appStore;
  const app = appStore?.m_mapApps?.get?.(appId);
  const gameId = app?.gameid;
  return typeof gameId === "string" && gameId.length > 0 ? gameId : null;
}

/**
 * Search Steam's in-memory app store for an entry whose launch options
 * contain the given store ID string.  This handles the case where the
 * backend's CRC32-computed appid differs from what Steam actually loaded
 * (e.g. after a frontend AddShortcut created a duplicate with a different id).
 */
function findAppByStoreId(storeId: string): number | null {
  const appStore = (window as any).appStore;
  const mapApps = appStore?.m_mapApps;
  if (!mapApps?.forEach) return null;

  let found: number | null = null;
  mapApps.forEach((app: any, appId: number) => {
    if (found !== null) return;
    try {
      const lo =
        app?.launch_options ??
        app?.strLaunchOptions ??
        app?.m_strLaunchOptions ??
        "";
      if (typeof lo === "string" && lo.includes(storeId)) {
        found = appId;
      }
    } catch {
      // skip
    }
  });
  return found;
}

async function waitForShortcut(
  appId: number,
  minimumDelayMs = 0,
): Promise<number | null> {
  const startedAt = Date.now();
  const timeoutMs = Math.max(SHORTCUT_POLL_TIMEOUT_MS, minimumDelayMs);

  return new Promise<number | null>((resolve) => {
    const poll = () => {
      const elapsedMs = Date.now() - startedAt;

      // First check by exact appId
      if (elapsedMs >= minimumDelayMs && isShortcutRegistered(appId)) {
        resolve(appId);
        return;
      }

      // Then try searching by store_id in case appid differs
      if (elapsedMs >= minimumDelayMs) {
        const foundId = findAppByStoreId(MS_AUTH_SHORTCUT_STORE_ID);
        if (foundId !== null) {
          console.log(
            `[MicrosoftShortcutLaunch] Found shortcut by store_id scan: ` +
              `expected=${appId}, actual=${foundId}`,
          );
          resolve(foundId);
          return;
        }
      }

      if (elapsedMs >= timeoutMs) {
        resolve(null);
        return;
      }
      window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
    };
    window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
  });
}

async function waitForShortcutGameId(
  appId: number,
  minimumDelayMs = 0,
): Promise<string | null> {
  const startedAt = Date.now();
  const timeoutMs = Math.max(SHORTCUT_POLL_TIMEOUT_MS, minimumDelayMs);

  return new Promise<string | null>((resolve) => {
    const poll = () => {
      const elapsedMs = Date.now() - startedAt;

      if (elapsedMs >= minimumDelayMs) {
        const gameId = getShortcutGameId(appId);
        if (gameId) {
          resolve(gameId);
          return;
        }
      }

      if (elapsedMs >= timeoutMs) {
        resolve(null);
        return;
      }

      window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
    };

    window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
  });
}

function schedulePersistentShortcutRepair(delayMs = 1000): void {
  window.setTimeout(() => {
    call("get_microsoft_auth_shortcut_context").catch((error) => {
      console.error(
        "[MicrosoftShortcutLaunch] Persistent shortcut repair failed:",
        error,
      );
    });
  }, delayMs);
}

function scheduleTemporaryShortcutCleanup(appId: number): void {
  const steamApps = window.SteamClient?.Apps;
  window.setTimeout(() => {
    try {
      steamApps?.RemoveShortcut?.(appId);
    } catch (error) {
      console.error(
        `[MicrosoftShortcutLaunch] Failed to remove temporary shortcut ${appId}:`,
        error,
      );
    }

    schedulePersistentShortcutRepair(TEMP_SHORTCUT_POST_REMOVE_REPAIR_DELAY_MS);
  }, TEMP_SHORTCUT_CLEANUP_DELAY_MS);
}

async function createTemporaryAuthShortcut(
  launcherPath: string,
  temporaryLaunchOptions: string,
): Promise<number | null> {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.AddShortcut) {
    return null;
  }

  const startDir = launcherPath.substring(0, launcherPath.lastIndexOf("/"));

  const newAppId = await steamApps.AddShortcut(
    "Microsoft Sign-In",
    launcherPath,
    startDir,
    temporaryLaunchOptions,
  );

  if (typeof newAppId !== "number" || newAppId <= 0) {
    return null;
  }

  steamApps.SetShortcutName?.(newAppId, "Microsoft Sign-In");
  steamApps.SetShortcutStartDir?.(newAppId, startDir);
  steamApps.SetShortcutLaunchOptions?.(newAppId, temporaryLaunchOptions);

  const gameId = await waitForShortcutGameId(newAppId, SHORTCUT_POLL_DELAY_MS);
  if (!gameId) {
    console.error(
      `[MicrosoftShortcutLaunch] Temporary shortcut ${newAppId} never received a gameid`,
    );
    try {
      steamApps.RemoveShortcut?.(newAppId);
    } catch (error) {
      console.error(
        `[MicrosoftShortcutLaunch] Failed to clean up temporary shortcut ${newAppId}:`,
        error,
      );
    }
    schedulePersistentShortcutRepair();
    return null;
  }

  console.log(
    `[MicrosoftShortcutLaunch] Temporary auth shortcut ready: ` +
      `appId=${newAppId}, gameId=${gameId}`,
  );
  return newAppId;
}

export async function launchMicrosoftAuthViaShortcut(): Promise<MicrosoftShortcutLaunchResult> {
  console.log("[MicrosoftShortcutLaunch] Starting auth shortcut launch flow");

  const authContext = await call<
    [],
    {
      success: boolean;
      appid_unsigned?: number;
      launch_wait_ms?: number;
      launcher_path?: string;
      launch_options?: string;
      error?: string;
    }
  >("get_microsoft_auth_shortcut_context");

  if (!authContext?.success || !authContext.appid_unsigned) {
    console.error(
      "[MicrosoftShortcutLaunch] Auth context failed:",
      authContext?.error,
    );
    return {
      success: false,
      error: authContext?.error || "Auth shortcut not available",
    };
  }

  const backendAppId = authContext.appid_unsigned;
  console.log(
    `[MicrosoftShortcutLaunch] Auth context received: appId=${backendAppId}, ` +
      `launchWait=${authContext.launch_wait_ms}ms`,
  );

  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame || !steamApps?.SetShortcutLaunchOptions) {
    return {
      success: false,
      error: "Steam shortcut launch APIs are unavailable",
    };
  }

  // Wait for the shortcut to appear in Steam's app store.
  // Searches by both appId AND store_id to handle mismatches.
  const resolvedAppId = await waitForShortcut(
    backendAppId,
    authContext.launch_wait_ms ?? 0,
  );
  let appId = resolvedAppId;
  let usedTemporaryShortcut = false;

  const temporaryLaunchOptions =
    `${MS_AUTH_SHORTCUT_STORE_ID} UNIFIDECK_MICROSOFT_ACTION=auth`;

  if (appId === null) {
    if (!authContext.launcher_path) {
      console.error(
        `[MicrosoftShortcutLaunch] Shortcut not loaded in Steam memory and ` +
          `launcher path unavailable: expectedAppId=${backendAppId}`,
      );
      return {
        success: false,
        error:
          "Microsoft Sign-In is not loaded in Steam yet. Restart Steam once and try again.",
      };
    }

    const tempStoreId =
      `${TEMP_MS_AUTH_SHORTCUT_STORE_ID_PREFIX}-${Date.now()}`;
    const tempLaunchOptions =
      `${tempStoreId} UNIFIDECK_MICROSOFT_ACTION=auth`;

    console.log(
      `[MicrosoftShortcutLaunch] Shortcut not loaded in Steam memory — ` +
        `creating temporary auth shortcut`,
    );

    const temporaryAppId = await createTemporaryAuthShortcut(
      authContext.launcher_path,
      tempLaunchOptions,
    );
    if (temporaryAppId === null) {
      return {
        success: false,
        error:
          "Microsoft Sign-In could not be prepared in Steam. Restart Steam once and try again.",
      };
    }

    appId = temporaryAppId;
    usedTemporaryShortcut = true;
    schedulePersistentShortcutRepair();
  }

  const alreadyRunning = isShortcutAppRunning(appId);

  // Fetch current launch options to restore after launch
  const shortcutContext = await call<[string], ShortcutLaunchContext>(
    "get_compat_tool_for_game",
    MS_AUTH_SHORTCUT_STORE_ID,
  ).catch(() => ({ success: false } as ShortcutLaunchContext));

  const originalLaunchOptions =
    getShortcutLaunchOptionsValue(appId) ??
    shortcutContext.current_launch_options ??
    temporaryLaunchOptions;

  try {
    steamApps.SpecifyCompatTool?.(appId, "");
    steamApps.SetShortcutLaunchOptions(appId, temporaryLaunchOptions);

    const runGameId = getShortcutRunGameId(appId);
    console.log(
      `[MicrosoftShortcutLaunch] Calling RunGame: appId=${appId}, ` +
        `runGameId=${runGameId}, launchOpts="${temporaryLaunchOptions}"`,
    );
    steamApps.RunGame(runGameId, "", -1, 100);

    if (usedTemporaryShortcut) {
      scheduleTemporaryShortcutCleanup(appId);
    } else {
      window.setTimeout(() => {
        steamApps.SetShortcutLaunchOptions?.(appId, originalLaunchOptions);
      }, 2000);
    }

    console.log(
      `[MicrosoftShortcutLaunch] ✓ Auth launched via RunGame (appId=${appId})`,
    );
    return { success: true, already_running: alreadyRunning, appId };
  } catch (error) {
    console.error("[MicrosoftShortcutLaunch] Shortcut launch failed:", error);
    steamApps.SetShortcutLaunchOptions?.(appId, originalLaunchOptions);
    return {
      success: false,
      error:
        error instanceof Error ? error.message : "Failed to launch shortcut",
    };
  }
}
