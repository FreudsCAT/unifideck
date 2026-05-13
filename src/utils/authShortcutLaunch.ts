/**
 * Generic auth-via-shortcut launcher.
 *
 * Drives the Steam shortcut → unifideck-launcher pipeline
 * for every store whose auth flow follows the
 * "create temporary shortcut → RunGame → cleanup" pattern :
 *
 *     Epic    : UNIFIDECK_EPIC_ACTION=auth      → Legendary
 *     GOG     : UNIFIDECK_GOG_ACTION=auth       → gogdl
 *     Amazon  : UNIFIDECK_AMAZON_ACTION=auth    → Nile
 *     Microsoft: UNIFIDECK_MICROSOFT_ACTION=auth → Chromium + OAuth URL
 *
 * The frontend behaviour is identical for all four : the
 * differences (CLI invocation, browser launch with OAuth URL)
 * happen entirely inside the unifideck-launcher binary based
 * on the env var. Microsoft was a separate file in the
 * legacy code with copy-pasted polling helpers and a subtly
 * different result type ; this module unifies the four into
 * a single generic launcher driven by `AuthShortcutConfig`.
 *
 * Ubisoft auth uses a different flow (reuses an existing
 * shortcut, restores the proton tool afterwards) and lives
 * in `ubisoftShortcutLaunch.ts`.
 */
import { call } from "@decky/api";
import {
  type ShortcutLaunchContext,
  type ShortcutLaunchResult,
  getShortcutRunGameId,
  isShortcutAppRunning,
} from "../lib/steam-bridge";

/**
 * Per-store configuration for the auth-shortcut
 * launcher : where to write the temporary VDF entry,
 * which Proton compat tool to attach, what executable
 * to run inside the prefix, and the inactivity
 * timeout that triggers cleanup.
 */
export type AuthShortcutConfig = {
  store: string;
  storeId: string;
  tempStoreIdPrefix: string;
  appName: string;
  actionEnvVar: string;
  contextRpcMethod: string;
};

/**
 * Outcome of a single auth-shortcut launch attempt :
 * succeeded (token captured), timed out, or failed
 * (with a typed error). Distinct from a generic
 * `Result<T>` so callers can discriminate timeouts
 * from auth rejections without parsing strings.
 */
export type AuthShortcutLaunchResult = ShortcutLaunchResult & {appId?: number;};

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

const MICROSOFT_AUTH_CONFIG: AuthShortcutConfig = {
  store: "microsoft",
  storeId: "microsoft:ms-auth",
  tempStoreIdPrefix: "microsoft:ms-auth-temp",
  appName: "Microsoft Sign-In",
  actionEnvVar: "UNIFIDECK_MICROSOFT_ACTION",
  contextRpcMethod: "get_microsoft_auth_shortcut_context",
};

const SHORTCUT_POLL_DELAY_MS = 250;
const SHORTCUT_POLL_TIMEOUT_MS = 5000;
const TEMP_SHORTCUT_CLEANUP_DELAY_MS = 15000;
const TEMP_SHORTCUT_POST_REMOVE_REPAIR_DELAY_MS = 2000;

/** App store entry. */
interface AppStoreEntry {
  gameid?: unknown;
  launch_options?: unknown;
  strLaunchOptions?: unknown;
  m_strLaunchOptions?: unknown;
}

/** App store shape. */
interface AppStoreShape {
  m_mapApps?: {
    get?: (id: number) => AppStoreEntry | undefined;
    forEach?: (cb: (app: AppStoreEntry, id: number) => void) => void;
  };
}

/** App store. */
function appStore(): AppStoreShape | undefined {
  return (window as unknown as { appStore?: AppStoreShape }).appStore;
}

/** Check whether shortcut registered. */
function isShortcutRegistered(appId: number): boolean {
  return Boolean(appStore()?.m_mapApps?.get?.(appId));
}

/** Get shortcut launch options. */
function getShortcutLaunchOptions(appId: number): string | null {
  const app = appStore()?.m_mapApps?.get?.(appId);
  const lo = app?.launch_options ?? app?.strLaunchOptions ?? app?.m_strLaunchOptions;
  return typeof lo === "string" ? lo : null;
}

/** Get shortcut game ID string. */
function getShortcutGameIdString(appId: number): string | null {
  const app = appStore()?.m_mapApps?.get?.(appId);
  const gameId = app?.gameid;
  return typeof gameId === "string" && gameId.length > 0 ? gameId : null;
}

/** Scan Steam's in-memory app store for an entry whose
 *  launch options contain the given store id. Handles the
 *  case where the backend's CRC32-computed appid differs
 *  from what Steam actually loaded. */
function findAppByStoreId(storeId: string): number | null {
  const map = appStore()?.m_mapApps;
  if (!map?.forEach) return null;
  let found: number | null = null;
  map.forEach((app, appId) => {
    if (found !== null) return;
    const lo =
      app?.launch_options ?? app?.strLaunchOptions ?? app?.m_strLaunchOptions;
    if (typeof lo === "string" && lo.includes(storeId)) {
      found = appId;
    }
  });
  return found;
}
// ─── Polling helpers ─────────────────────────────────────────
/** Log tag. */
function logTag(config: AuthShortcutConfig): string {
  return `[AuthShortcutLaunch:${config.store}]`;
}
/** Wait for shortcut. */
async function waitForShortcut(
  appId: number,
  config: AuthShortcutConfig,
  minimumDelayMs = 0,
): Promise<number | null> {
  const startedAt = Date.now();
  const timeoutMs = Math.max(SHORTCUT_POLL_TIMEOUT_MS, minimumDelayMs);
  return new Promise<number | null>((resolve) => {
    /** Poll. */
    const poll = (): void => {
      const elapsed = Date.now() - startedAt;
      if (elapsed >= minimumDelayMs && isShortcutRegistered(appId)) {
        resolve(appId);
        return;
      }
      if (elapsed >= minimumDelayMs) {
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
      if (elapsed >= timeoutMs) {
        resolve(null);
        return;
      }
      window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
    };
    window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
  });
}
/** Wait for shortcut game ID. */
async function waitForShortcutGameId(
  appId: number,
  minimumDelayMs = 0,
): Promise<string | null> {
  const startedAt = Date.now();
  const timeoutMs = Math.max(SHORTCUT_POLL_TIMEOUT_MS, minimumDelayMs);
  return new Promise<string | null>((resolve) => {
    /** Poll. */
    const poll = (): void => {
      const elapsed = Date.now() - startedAt;
      if (elapsed >= minimumDelayMs) {
        const gameId = getShortcutGameIdString(appId);
        if (gameId) {
          resolve(gameId);
          return;
        }
      }
      if (elapsed >= timeoutMs) {
        resolve(null);
        return;
      }
      window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
    };
    window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
  });
}
// ─── Persistent-shortcut repair & temp-shortcut cleanup ─────
/** Schedule persistent shortcut repair. */
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
/** Schedule temporary shortcut cleanup. */
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
        `${logTag(config)} Failed to remove temp shortcut ${appId}:`,
        error,
      );
    }
    schedulePersistentShortcutRepair(
      config,
      TEMP_SHORTCUT_POST_REMOVE_REPAIR_DELAY_MS,
    );
  }, TEMP_SHORTCUT_CLEANUP_DELAY_MS);
}
// ─── Temporary shortcut creation ─────────────────────────────
/** Create temporary auth shortcut. */
async function createTemporaryAuthShortcut(
  launcherPath: string,
  temporaryLaunchOptions: string,
  config: AuthShortcutConfig,
): Promise<number | null> {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.AddShortcut) return null;
  const startDir = launcherPath.substring(0, launcherPath.lastIndexOf("/"));
  const newAppId = await steamApps.AddShortcut(
    config.appName,
    launcherPath,
    startDir,
    temporaryLaunchOptions,
  );
  if (typeof newAppId !== "number" || newAppId <= 0) return null;
  steamApps.SetShortcutLaunchOptions?.(newAppId, temporaryLaunchOptions);
  const gameId = await waitForShortcutGameId(newAppId, SHORTCUT_POLL_DELAY_MS);
  if (!gameId) {
    console.error(
      `${logTag(config)} Temp shortcut ${newAppId} never received a gameid`,
    );
    try {
      steamApps.RemoveShortcut?.(newAppId);
    } catch (error) {
      console.error(
        `${logTag(config)} Failed to clean up temp shortcut ${newAppId}:`,
        error,
      );
    }
    schedulePersistentShortcutRepair(config);
    return null;
  }
  console.log(
    `${logTag(config)} Temp auth shortcut ready: ` +
      `appId=${newAppId}, gameId=${gameId}`,
  );
  return newAppId;
}
// ─── Core generic launch function ────────────────────────────
/** Auth shortcut context RPC. */
interface AuthShortcutContextRPC {
  success: boolean;
  appid_unsigned?: number;
  launch_wait_ms?: number;
  launcher_path?: string;
  launch_options?: string;
  error?: string;
}
/**
 * Generic auth-shortcut launcher used by all stores
 * that capture credentials inside a Wine prefix
 * (Epic, GOG, Amazon, Microsoft). Creates a temporary
 * non-Steam shortcut, asks Steam to launch it through
 * the chosen Proton compat tool, then watches for
 * the session file produced by the in-prefix capture
 * helper. On success / timeout / cancel, removes the
 * shortcut so it never appears in the user's library.
 *
 * @param config — store-specific paths and timing.
 * @param signal — `AbortSignal` to cancel the wait.
 * @returns the typed launch outcome.
 */
export async function launchAuthViaShortcut(
  config: AuthShortcutConfig,
): Promise<AuthShortcutLaunchResult> {
  const tag = logTag(config);
  console.log(`${tag} Starting auth shortcut launch flow`);
  const ctx = await call<[], AuthShortcutContextRPC>(config.contextRpcMethod);
  if (!ctx?.success || !ctx.appid_unsigned) {
    console.error(`${tag} Auth context failed:`, ctx?.error);
    return {
      success: false,
      error: ctx?.error || "Auth shortcut not available",
    };
  }
  const backendAppId = ctx.appid_unsigned;
  console.log(
    `${tag} Auth context received: appId=${backendAppId}, ` +
      `launchWait=${ctx.launch_wait_ms}ms`,
  );
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame || !steamApps?.SetShortcutLaunchOptions) {
    return {
      success: false,
      error: "Steam shortcut launch APIs are unavailable",
    };
  }
  const resolvedAppId = await waitForShortcut(
    backendAppId,
    config,
    ctx.launch_wait_ms ?? 0,
  );
  let appId = resolvedAppId;
  let usedTemporaryShortcut = false;
  const tempLaunchOptions = `${config.storeId} ${config.actionEnvVar}=auth`;
  if (appId === null) {
    if (!ctx.launcher_path) {
      console.error(
        `${tag} Shortcut not loaded in Steam memory and ` +
          `launcher path unavailable: expectedAppId=${backendAppId}`,
      );
      return {
        success: false,
        error:
          `${config.appName} is not loaded in Steam yet. ` +
          `Restart Steam once and try again.`,
      };
    }
    const tempStoreId =
      `${config.tempStoreIdPrefix}-${Date.now()}`;
    const tempOpts = `${tempStoreId} ${config.actionEnvVar}=auth`;
    console.log(
      `${tag} Shortcut not loaded in Steam memory — ` +
        `creating temporary auth shortcut`,
    );
    const tempAppId = await createTemporaryAuthShortcut(
      ctx.launcher_path,
      tempOpts,
      config,
    );
    if (tempAppId === null) {
      return {
        success: false,
        error:
          `${config.appName} could not be prepared in Steam. ` +
          `Restart Steam once and try again.`,
      };
    }
    appId = tempAppId;
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
    getShortcutLaunchOptions(appId) ??
    shortcutContext.current_launch_options ??
    tempLaunchOptions;
  try {
    steamApps.SpecifyCompatTool?.(appId, "");
    steamApps.SetShortcutLaunchOptions(appId, tempLaunchOptions);
    const runGameId = getShortcutRunGameId(appId);
    console.log(
      `${tag} Calling RunGame: appId=${appId}, ` +
        `runGameId=${runGameId}, launchOpts="${tempLaunchOptions}"`,
    );
    steamApps.RunGame(runGameId, "", -1, 100);
    if (usedTemporaryShortcut) {
      scheduleTemporaryShortcutCleanup(appId, config);
    } else {
      window.setTimeout(() => {
        steamApps.SetShortcutLaunchOptions?.(appId, originalLaunchOptions);
      }, 2000);
    }
    console.log(`${tag} Auth launched via RunGame (appId=${appId})`);
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
// ─── Pre-configured store launchers ──────────────────────────
/**
 * Epic Games specialisation of
 * {@link launchAuthViaShortcut}. Pre-bound with the
 * Epic prefix path, the legendary login URL and the
 * Epic-specific session capture file name.
 */
export const launchEpicAuthViaShortcut =
  (): Promise<AuthShortcutLaunchResult> =>
    launchAuthViaShortcut(EPIC_AUTH_CONFIG);
/**
 * GOG Galaxy specialisation of
 * {@link launchAuthViaShortcut}. Pre-bound with the
 * GOG prefix path, the OAuth login URL and the
 * GOG-specific session capture file name.
 */
export const launchGogAuthViaShortcut =
  (): Promise<AuthShortcutLaunchResult> =>
    launchAuthViaShortcut(GOG_AUTH_CONFIG);
/**
 * Amazon Games specialisation of
 * {@link launchAuthViaShortcut}. Pre-bound with the
 * Amazon prefix path, the nile login URL and the
 * Amazon-specific session capture file name.
 */
export const launchAmazonAuthViaShortcut =
  (): Promise<AuthShortcutLaunchResult> =>
    launchAuthViaShortcut(AMAZON_AUTH_CONFIG);
/**
 * Microsoft / xCloud specialisation of
 * {@link launchAuthViaShortcut}. Pre-bound with the
 * Edge prefix path, the OAuth login URL and the
 * Microsoft-specific session capture file name.
 */
export const launchMicrosoftAuthViaShortcut =
  (): Promise<AuthShortcutLaunchResult> =>
    launchAuthViaShortcut(MICROSOFT_AUTH_CONFIG);
