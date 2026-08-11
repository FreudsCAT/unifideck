/**
 * Shortcut launching for *wrapper stores*.
 *
 * src/lib/steam-bridge/wrapper-shortcut-launch.ts
 *
 * A wrapper store runs a vendor's own Windows client inside a Proton prefix
 * (Ubisoft Connect, Battle.net, and EA App next). They all need the same
 * dance, because the backend cannot spawn the client itself — in Gaming
 * Mode a bare subprocess has no gamescope session and the window never
 * appears, so the frontend must `RunGame` a Steam shortcut instead.
 *
 * Everything here was previously duplicated per store. Only a handful of
 * values actually differ, so they live in `WrapperShortcutConfig` and the
 * ~250 lines of behaviour are shared. Adding EA App should be a config
 * object, not another copy of this file.
 *
 * The subtle parts, all of which are load-bearing:
 *
 *  - **User launch params are preserved.** `extractUserParams` strips our
 *    env tokens, the store_game_id and the launcher path, keeping the
 *    user's `mangohud` / `gamemoderun` / `#%command%` wrappers.
 *  - **First-session shortcuts need a temporary stand-in.** Steam only
 *    loads `shortcuts.vdf` at startup, so a shortcut the backend just
 *    wrote is absent from `appStore.m_mapApps` and `RunGame` on its appid
 *    fails with "Game configuration unavailable". `AddShortcut` registers
 *    one immediately.
 *  - **`skipStateRestore` exists for auth.** An auth shortcut's temporary
 *    options ARE its canonical persistent options, so restoring the
 *    (empty) originals wiped them and produced a tile that opened and
 *    instantly closed.
 */

import { call } from "@decky/api";
import {
  type ShortcutLaunchContext,
  type ShortcutLaunchResult,
  getShortcutRunGameId,
  isShortcutAppRunning,
} from "./shortcut-types";
import {
  createTemporaryShortcut,
  scheduleTemporaryShortcutCleanup,
} from "./temp-shortcut";
import { rpcRoutes } from "../../api/rpc-routes";
import { unwrapRpcEnvelope } from "../../api/useRPC";

const RESTORE_POLL_DELAY_MS = 250;
const RESTORE_START_DELAY_MS = 500;
const RESTORE_TIMEOUT_MS = 5000;
const SHORTCUT_REGISTRATION_POLL_DELAY_MS = 250;

/** Everything that differs between one wrapper store and another. */
export interface WrapperShortcutConfig {
  /** Store id as used in `store:game_id` launch options. */
  storeId: string;
  /** Shortcut display name, e.g. "Ubisoft Connect". */
  displayName: string;
  /** Log prefix, e.g. "[UbisoftShortcutLaunch]". */
  logTag: string;
  /** Auth shortcut's store_game_id, e.g. "ubisoft:upc-auth". */
  authShortcutStoreId: string;
  /** RPC route returning the auth shortcut context. */
  authContextRoute: string;
  /** Env var carrying the action, e.g. "UNIFIDECK_UBISOFT_ACTION". */
  actionEnvVar: string;
  /**
   * Optional env var naming the auth prefix.
   *
   * Required whenever the auth prefix is not named after the id in
   * `authShortcutStoreId`. Both wrapper stores need it: the launcher
   * derives the prefix from `ctx.game_id` otherwise, and an auth prefix
   * has no id-map record to resolve instead — so sign-in silently targets
   * an empty directory.
   */
  prefixEnvVar?: string;
  /** Value for `prefixEnvVar`, e.g. ".upc-auth". */
  authPrefixName?: string;
}

interface AuthShortcutContext {
  appid_unsigned?: number;
  launch_wait_ms?: number;
  launcher_path?: string;
  error?: string;
}

/** Escape a string for literal use inside a RegExp. */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Strip Unifideck env tokens, the store_game_id and the launcher path from
 * the user's launch_options, keeping only their own wrappers.
 */
export function extractUserParams(
  launchOptions: string,
  storeGameId: string,
  launcherPath?: string,
): string {
  let cleaned = launchOptions.replace(/\s*#%command%\s*$/g, "");
  const escaped = escapeRegExp(storeGameId);
  cleaned = cleaned.replace(/\bUNIFIDECK_[A-Z0-9_]+=(?:"[^"]*"|\S+)/g, "");
  cleaned = cleaned
    .replace(new RegExp(`"${escaped}"`, "g"), "")
    .replace(new RegExp(`(?<=^|\\s)${escaped}(?=\\s|$)`, "g"), "");
  if (launcherPath) {
    const escLauncher = escapeRegExp(launcherPath);
    cleaned = cleaned
      .replace(new RegExp(`"${escLauncher}"`, "g"), "")
      .replace(new RegExp(escLauncher, "g"), "");
  }
  return cleaned.replace(/\s{2,}/g, " ").trim();
}

/** Compose the launch options string for one run. */
export function buildTemporaryLaunchOptions(
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

interface AppStoreEntry {
  display_name?: unknown;
}

interface AppStoreShape {
  m_mapApps?: { get?: (id: number) => AppStoreEntry | undefined };
}

function appStore(): AppStoreShape | undefined {
  return (window as unknown as { appStore?: AppStoreShape }).appStore;
}

/** Whether Steam has this shortcut in its in-memory app store. */
export function isShortcutRegistered(appId: number): boolean {
  return Boolean(appStore()?.m_mapApps?.get?.(appId));
}

/**
 * Wait (at most `minimumDelayMs`) for Steam to register a persistent
 * shortcut. Steam only loads `shortcuts.vdf` at startup, so one written
 * this session never appears — there is no point polling a long timeout.
 */
async function waitForShortcutRegistration(
  appId: number,
  minimumDelayMs = 0,
): Promise<void> {
  if (isShortcutRegistered(appId)) return;
  if (minimumDelayMs <= 0) return;
  const startedAt = Date.now();
  await new Promise<void>((resolve) => {
    const poll = (): void => {
      if (
        isShortcutRegistered(appId) ||
        Date.now() - startedAt >= minimumDelayMs
      ) {
        resolve();
        return;
      }
      window.setTimeout(poll, SHORTCUT_REGISTRATION_POLL_DELAY_MS);
    };
    window.setTimeout(poll, SHORTCUT_REGISTRATION_POLL_DELAY_MS);
  });
}

/** Force-stop a shortcut launch via Steam's TerminateApp. */
export function terminateShortcutApp(
  appId: number,
  logTag = "[WrapperShortcut]",
): boolean {
  try {
    window.SteamClient?.Apps?.TerminateApp?.(
      getShortcutRunGameId(appId),
      false,
    );
    return true;
  } catch (error) {
    console.error(`${logTag} terminateShortcutApp failed for ${appId}:`, error);
    return false;
  }
}

/**
 * Restore the user's compat tool and launch options once Steam has picked
 * up the RunGame call, polling until the app reports running so we do not
 * clobber a still-applying launch.
 */
function scheduleLaunchStateRestore(
  appId: number,
  context: ShortcutLaunchContext,
  originalLaunchOptions: string,
  logTag: string,
): void {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps) return;
  const startedAt = Date.now();
  const targetTool = context.saved_proton_tool ?? "";

  const tryRestore = (): void => {
    const elapsed = Date.now() - startedAt;
    if (elapsed < RESTORE_START_DELAY_MS) {
      window.setTimeout(tryRestore, RESTORE_POLL_DELAY_MS);
      return;
    }
    if (!isShortcutAppRunning(appId) && elapsed < RESTORE_TIMEOUT_MS) {
      window.setTimeout(tryRestore, RESTORE_POLL_DELAY_MS);
      return;
    }
    try {
      steamApps.SpecifyCompatTool?.(appId, targetTool);
      steamApps.SetShortcutLaunchOptions?.(appId, originalLaunchOptions);
    } catch (error) {
      console.error(`${logTag} Restore failed for appId=${appId}:`, error);
    }
  };
  window.setTimeout(tryRestore, RESTORE_START_DELAY_MS);
}

/** Launch via a freshly created throwaway shortcut. */
async function launchViaTemporaryShortcut(
  config: WrapperShortcutConfig,
  ctx: ShortcutLaunchContext,
  launchOptions: string,
): Promise<ShortcutLaunchResult> {
  const steamApps = window.SteamClient?.Apps;
  const launcherPath = ctx.launcher_path;
  if (!steamApps?.RunGame || !launcherPath) {
    return {
      success: false,
      error: "Steam launch APIs or launcher path unavailable",
    };
  }
  const tempAppId = await createTemporaryShortcut({
    appName: config.displayName,
    launcherPath,
    launchOptions,
    logTag: config.logTag,
  });
  if (tempAppId === null) {
    return {
      success: false,
      error:
        `${config.displayName} could not be prepared in Steam. ` +
        "Restart Steam once and try again.",
    };
  }
  const alreadyRunning = isShortcutAppRunning(tempAppId);
  try {
    steamApps.SpecifyCompatTool?.(tempAppId, ctx.tool_name ?? "");
    steamApps.SetShortcutLaunchOptions?.(tempAppId, launchOptions);
    steamApps.RunGame(getShortcutRunGameId(tempAppId), "", -1, 100);
    scheduleTemporaryShortcutCleanup(tempAppId, config.logTag);
    return {
      success: true,
      already_running: alreadyRunning,
      app_id: tempAppId,
    };
  } catch (error) {
    console.error(`${config.logTag} temp shortcut launch failed:`, error);
    return {
      success: false,
      error:
        error instanceof Error ? error.message : "Failed to launch shortcut",
    };
  }
}

/**
 * Launch a wrapper-store action through its Steam shortcut.
 *
 * `skipStateRestore` must be true for auth flows: the auth shortcut's
 * temporary options are its canonical persistent options, and restoring
 * the empty originals leaves Steam launching the bare launcher with no
 * arguments.
 */
export async function launchWrapperViaShortcut(
  config: WrapperShortcutConfig,
  storeGameId: string,
  extraEnv: Record<string, string> = {},
  contextOverride?: Partial<ShortcutLaunchContext>,
  skipStateRestore = false,
): Promise<ShortcutLaunchResult> {
  const rawCtx = await call<[string], unknown>(
    rpcRoutes.getCompatToolForGame,
    storeGameId,
  ).catch(() => null);
  const baseCtx =
    rawCtx == null
      ? ({} as ShortcutLaunchContext)
      : unwrapRpcEnvelope<ShortcutLaunchContext>(rawCtx, {
          route: rpcRoutes.getCompatToolForGame,
          throwing: false,
        });
  // `contextOverride` wins: the auth flow resolves the shortcut's appid via
  // the dedicated auth route (which ensures the shortcut exists and repairs
  // the VDF), whereas getCompatToolForGame only reads an already-registered
  // game and returns no appid for an auth shortcut.
  const ctx = { ...baseCtx, ...contextOverride } as ShortcutLaunchContext;

  if (!ctx.appid_unsigned) {
    console.error(`${config.logTag} context unavailable:`, ctx);
    return { success: false, error: ctx?.error || "Context unavailable" };
  }

  const appId = ctx.appid_unsigned;
  await waitForShortcutRegistration(appId, ctx.launch_wait_ms ?? 0);
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame || !steamApps?.SetShortcutLaunchOptions) {
    return { success: false, error: "Steam launch APIs unavailable" };
  }

  const originalOptions = ctx.current_launch_options ?? "";
  const tempOptions = buildTemporaryLaunchOptions(ctx, extraEnv, storeGameId);

  if (!isShortcutRegistered(appId)) {
    console.log(
      `${config.logTag} appId=%d not in Steam's app store; using temp shortcut`,
      appId,
    );
    return launchViaTemporaryShortcut(config, ctx, tempOptions);
  }

  const alreadyRunning = isShortcutAppRunning(appId);
  try {
    steamApps.SpecifyCompatTool?.(appId, ctx.tool_name ?? "");
    steamApps.SetShortcutLaunchOptions(appId, tempOptions);
    steamApps.RunGame(getShortcutRunGameId(appId), "", -1, 100);
    if (!skipStateRestore) {
      scheduleLaunchStateRestore(appId, ctx, originalOptions, config.logTag);
    }
    return { success: true, already_running: alreadyRunning, app_id: appId };
  } catch (error) {
    console.error(`${config.logTag} launch failed:`, error);
    if (!skipStateRestore) {
      steamApps.SetShortcutLaunchOptions?.(appId, originalOptions);
    }
    return {
      success: false,
      error:
        error instanceof Error ? error.message : "Failed to launch shortcut",
    };
  }
}

/** Env tokens for one action, including the optional prefix hint. */
export function wrapperActionEnv(
  config: WrapperShortcutConfig,
  action: string,
): Record<string, string> {
  const env: Record<string, string> = { [config.actionEnvVar]: action };
  if (config.prefixEnvVar && config.authPrefixName && action === "auth") {
    env[config.prefixEnvVar] = config.authPrefixName;
  }
  return env;
}

/**
 * Open the vendor client so the user can sign in.
 *
 * Resolves the persistent auth shortcut through the store's dedicated
 * context route, which ensures the shortcut exists and repairs the VDF.
 */
export async function launchWrapperAuthViaShortcut(
  config: WrapperShortcutConfig,
): Promise<ShortcutLaunchResult> {
  const raw = await call<[], unknown>(config.authContextRoute).catch(
    () => null,
  );
  const authCtx =
    raw == null
      ? undefined
      : unwrapRpcEnvelope<AuthShortcutContext>(raw, {
          route: config.authContextRoute,
          throwing: false,
        });
  if (!authCtx?.appid_unsigned) {
    console.error(
      `${config.logTag} auth shortcut context unavailable:`,
      authCtx,
    );
    return {
      success: false,
      error: authCtx?.error || "Auth shortcut not available",
    };
  }
  // The action env var must be re-supplied explicitly:
  // buildTemporaryLaunchOptions strips every UNIFIDECK_* token from the
  // shortcut's stored options, so without this the launcher treats the run
  // as a game launch rather than a sign-in.
  return launchWrapperViaShortcut(
    config,
    config.authShortcutStoreId,
    wrapperActionEnv(config, "auth"),
    {
      appid_unsigned: authCtx.appid_unsigned,
      launch_wait_ms: authCtx.launch_wait_ms,
      launcher_path: authCtx.launcher_path,
    },
    // Keep the canonical auth LaunchOptions instead of wiping them.
    true,
  );
}
