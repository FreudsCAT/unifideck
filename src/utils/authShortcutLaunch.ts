/**
 * Generic auth-via-shortcut launcher for store connectors.
 *
 * Generalises the Microsoft auth shortcut pattern so that Epic, GOG, and
 * Amazon auth flows can reuse the same launch → poll → temp-shortcut →
 * RunGame → cleanup pipeline without duplicating logic.
 */
import { call } from "@decky/api";
import {
  type ShortcutLaunchContext,
  type ShortcutLaunchResult,
  getShortcutRunGameId,
  isShortcutAppRunning,
} from "./ubisoftShortcutLaunch";

// ---------------------------------------------------------------------------
// Config & result types
// ---------------------------------------------------------------------------

export type AuthShortcutConfig = {
  store: string;
  storeId: string;
  tempStoreIdPrefix: string;
  appName: string;
  actionEnvVar: string;
  contextRpcMethod: string;
};

export type AuthShortcutLaunchResult = ShortcutLaunchResult & {
  appId?: number;
};

// ---------------------------------------------------------------------------
// Per-store configs
// ---------------------------------------------------------------------------

const EPIC_AUTH_CONFIG: AuthShortcutConfig = {
  store: "epic",
  storeId: "epic:epic-auth",
  tempStoreIdPrefix: "epic:epic-auth-temp",
  appName: "Epic Games Sign-In",
  actionEnvVar: "UNIFIDECK_EPIC_ACTION",
  contextRpcMethod: "get_epic_auth_shortcut_context",
};

const GOG_AUTH_CONFIG: AuthShortcutConfig = {
  store: "gog",
  storeId: "gog:gog-auth",
  tempStoreIdPrefix: "gog:gog-auth-temp",
  appName: "GOG Sign-In",
  actionEnvVar: "UNIFIDECK_GOG_ACTION",
  contextRpcMethod: "get_gog_auth_shortcut_context",
};

const AMAZON_AUTH_CONFIG: AuthShortcutConfig = {
  store: "amazon",
  storeId: "amazon:amazon-auth",
  tempStoreIdPrefix: "amazon:amazon-auth-temp",
  appName: "Amazon Games Sign-In",
  actionEnvVar: "UNIFIDECK_AMAZON_ACTION",
  contextRpcMethod: "get_amazon_auth_shortcut_context",
};

// ---------------------------------------------------------------------------
// Timing constants (shared across all stores)
// ---------------------------------------------------------------------------

const SHORTCUT_POLL_DELAY_MS = 250;
const SHORTCUT_POLL_TIMEOUT_MS = 5000;
const TEMP_SHORTCUT_CLEANUP_DELAY_MS = 15000;
const TEMP_SHORTCUT_POST_REMOVE_REPAIR_DELAY_MS = 2000;

// ---------------------------------------------------------------------------
// Steam app-store helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Polling helpers
// ---------------------------------------------------------------------------

function logTag(config: AuthShortcutConfig): string {
  return `[AuthShortcutLaunch:${config.store}]`;
}

async function waitForShortcut(
  appId: number,
  config: AuthShortcutConfig,
  minimumDelayMs = 0,
): Promise<number | null> {
  const startedAt = Date.now();
  const timeoutMs = Math.max(SHORTCUT_POLL_TIMEOUT_MS, minimumDelayMs);

  return new Promise<number | null>((resolve) => {
    const poll = () => {
      const elapsedMs = Date.now() - startedAt;

      if (elapsedMs >= minimumDelayMs && isShortcutRegistered(appId)) {
        resolve(appId);
        return;
      }

      if (elapsedMs >= minimumDelayMs) {
        const foundId = findAppByStoreId(config.storeId);
        if (foundId !== null) {
          console.log(
            `${logTag(config)} Found shortcut by store_id scan: ` +
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

// ---------------------------------------------------------------------------
// Persistent-shortcut repair & temporary-shortcut cleanup
// ---------------------------------------------------------------------------

function schedulePersistentShortcutRepair(
  config: AuthShortcutConfig,
  delayMs = 1000,
): void {
  window.setTimeout(() => {
    call(config.contextRpcMethod).catch((error) => {
      console.error(
        `${logTag(config)} Persistent shortcut repair failed:`,
        error,
      );
    });
  }, delayMs);
}

function scheduleTemporaryShortcutCleanup(
  appId: number,
  config: AuthShortcutConfig,
): void {
  const steamApps = window.SteamClient?.Apps;
  window.setTimeout(() => {
    try {
      steamApps?.RemoveShortcut?.(appId);
    } catch (error) {
      console.error(
        `${logTag(config)} Failed to remove temporary shortcut ${appId}:`,
        error,
      );
    }

    schedulePersistentShortcutRepair(
      config,
      TEMP_SHORTCUT_POST_REMOVE_REPAIR_DELAY_MS,
    );
  }, TEMP_SHORTCUT_CLEANUP_DELAY_MS);
}

// ---------------------------------------------------------------------------
// Temporary shortcut creation
// ---------------------------------------------------------------------------

async function createTemporaryAuthShortcut(
  launcherPath: string,
  temporaryLaunchOptions: string,
  config: AuthShortcutConfig,
): Promise<number | null> {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.AddShortcut) {
    return null;
  }

  const startDir = launcherPath.substring(0, launcherPath.lastIndexOf("/"));

  const newAppId = await steamApps.AddShortcut(
    config.appName,
    launcherPath,
    startDir,
    temporaryLaunchOptions,
  );

  if (typeof newAppId !== "number" || newAppId <= 0) {
    return null;
  }

  steamApps.SetShortcutName?.(newAppId, config.appName);
  steamApps.SetShortcutStartDir?.(newAppId, startDir);
  steamApps.SetShortcutLaunchOptions?.(newAppId, temporaryLaunchOptions);

  const gameId = await waitForShortcutGameId(newAppId, SHORTCUT_POLL_DELAY_MS);
  if (!gameId) {
    console.error(
      `${logTag(config)} Temporary shortcut ${newAppId} never received a gameid`,
    );
    try {
      steamApps.RemoveShortcut?.(newAppId);
    } catch (error) {
      console.error(
        `${logTag(config)} Failed to clean up temporary shortcut ${newAppId}:`,
        error,
      );
    }
    schedulePersistentShortcutRepair(config);
    return null;
  }

  console.log(
    `${logTag(config)} Temporary auth shortcut ready: ` +
      `appId=${newAppId}, gameId=${gameId}`,
  );
  return newAppId;
}

// ---------------------------------------------------------------------------
// Core generic launch function
// ---------------------------------------------------------------------------

export async function launchAuthViaShortcut(
  config: AuthShortcutConfig,
): Promise<AuthShortcutLaunchResult> {
  const tag = logTag(config);
  console.log(`${tag} Starting auth shortcut launch flow`);

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
  >(config.contextRpcMethod);

  if (!authContext?.success || !authContext.appid_unsigned) {
    console.error(`${tag} Auth context failed:`, authContext?.error);
    return {
      success: false,
      error: authContext?.error || "Auth shortcut not available",
    };
  }

  const backendAppId = authContext.appid_unsigned;
  console.log(
    `${tag} Auth context received: appId=${backendAppId}, ` +
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
  const resolvedAppId = await waitForShortcut(
    backendAppId,
    config,
    authContext.launch_wait_ms ?? 0,
  );
  let appId = resolvedAppId;
  let usedTemporaryShortcut = false;

  const temporaryLaunchOptions =
    `${config.storeId} ${config.actionEnvVar}=auth`;

  if (appId === null) {
    if (!authContext.launcher_path) {
      console.error(
        `${tag} Shortcut not loaded in Steam memory and ` +
          `launcher path unavailable: expectedAppId=${backendAppId}`,
      );
      return {
        success: false,
        error:
          `${config.appName} is not loaded in Steam yet. Restart Steam once and try again.`,
      };
    }

    const tempStoreId = `${config.tempStoreIdPrefix}-${Date.now()}`;
    const tempLaunchOptions = `${tempStoreId} ${config.actionEnvVar}=auth`;

    console.log(
      `${tag} Shortcut not loaded in Steam memory — ` +
        `creating temporary auth shortcut`,
    );

    const temporaryAppId = await createTemporaryAuthShortcut(
      authContext.launcher_path,
      tempLaunchOptions,
      config,
    );
    if (temporaryAppId === null) {
      return {
        success: false,
        error:
          `${config.appName} could not be prepared in Steam. Restart Steam once and try again.`,
      };
    }

    appId = temporaryAppId;
    usedTemporaryShortcut = true;
    schedulePersistentShortcutRepair(config);
  }

  const alreadyRunning = isShortcutAppRunning(appId);

  // Fetch current launch options to restore after launch
  const shortcutContext = await call<[string], ShortcutLaunchContext>(
    "get_compat_tool_for_game",
    config.storeId,
  ).catch(() => ({ success: false }) as ShortcutLaunchContext);

  const originalLaunchOptions =
    getShortcutLaunchOptionsValue(appId) ??
    shortcutContext.current_launch_options ??
    temporaryLaunchOptions;

  try {
    steamApps.SpecifyCompatTool?.(appId, "");
    steamApps.SetShortcutLaunchOptions(appId, temporaryLaunchOptions);

    const runGameId = getShortcutRunGameId(appId);
    console.log(
      `${tag} Calling RunGame: appId=${appId}, ` +
        `runGameId=${runGameId}, launchOpts="${temporaryLaunchOptions}"`,
    );
    steamApps.RunGame(runGameId, "", -1, 100);

    if (usedTemporaryShortcut) {
      scheduleTemporaryShortcutCleanup(appId, config);
    } else {
      window.setTimeout(() => {
        steamApps.SetShortcutLaunchOptions?.(appId, originalLaunchOptions);
      }, 2000);
    }

    console.log(`${tag} ✓ Auth launched via RunGame (appId=${appId})`);
    return { success: true, already_running: alreadyRunning, appId };
  } catch (error) {
    console.error(`${tag} Shortcut launch failed:`, error);
    steamApps.SetShortcutLaunchOptions?.(appId, originalLaunchOptions);
    return {
      success: false,
      error:
        error instanceof Error ? error.message : "Failed to launch shortcut",
    };
  }
}

// ---------------------------------------------------------------------------
// Pre-configured store launchers
// ---------------------------------------------------------------------------

export const launchEpicAuthViaShortcut = () =>
  launchAuthViaShortcut(EPIC_AUTH_CONFIG);

export const launchGogAuthViaShortcut = () =>
  launchAuthViaShortcut(GOG_AUTH_CONFIG);

export const launchAmazonAuthViaShortcut = () =>
  launchAuthViaShortcut(AMAZON_AUTH_CONFIG);
