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
  type ShortcutLaunchResult,
  createTemporaryShortcut,
  getShortcutRunGameId,
  isShortcutAppRunning,
  scheduleTemporaryShortcutCleanup,
} from "../lib/steam-bridge";
import { rpcRoutes, type RouteName } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { applyWebBrowserLayout } from "./controllerConfig";

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
  /** Backend RPC route returning the auth-shortcut metadata
   *  ({appid_unsigned, launcher_path, launch_options,
   *  launch_wait_ms}). Sourced from `rpcRoutes` so a backend
   *  rename is a one-file change. */
  contextRpcMethod: RouteName;
};

/**
 * Outcome of a single auth-shortcut launch attempt :
 * succeeded (token captured), timed out, or failed
 * (with a typed error). Distinct from a generic
 * `Result<T>` so callers can discriminate timeouts
 * from auth rejections without parsing strings.
 */
export type AuthShortcutLaunchResult = ShortcutLaunchResult & {
  appId?: number;
};

const EPIC_AUTH_CONFIG: AuthShortcutConfig = {
  store: "epic",
  storeId: "epic:epic-auth",
  tempStoreIdPrefix: "epic:epic-auth-temp",
  appName: "Epic Games Sign-In",
  actionEnvVar: "UNIFIDECK_EPIC_ACTION",
  contextRpcMethod: rpcRoutes.getEpicAuthShortcutContext,
};

const GOG_AUTH_CONFIG: AuthShortcutConfig = {
  store: "gog",
  storeId: "gog:gog-auth",
  tempStoreIdPrefix: "gog:gog-auth-temp",
  appName: "GOG Sign-In",
  actionEnvVar: "UNIFIDECK_GOG_ACTION",
  contextRpcMethod: rpcRoutes.getGogAuthShortcutContext,
};

const AMAZON_AUTH_CONFIG: AuthShortcutConfig = {
  store: "amazon",
  storeId: "amazon:amazon-auth",
  tempStoreIdPrefix: "amazon:amazon-auth-temp",
  appName: "Amazon Games Sign-In",
  actionEnvVar: "UNIFIDECK_AMAZON_ACTION",
  contextRpcMethod: rpcRoutes.getAmazonAuthShortcutContext,
};

const MICROSOFT_AUTH_CONFIG: AuthShortcutConfig = {
  store: "microsoft",
  storeId: "microsoft:ms-auth",
  tempStoreIdPrefix: "microsoft:ms-auth-temp",
  appName: "Microsoft Sign-In",
  actionEnvVar: "UNIFIDECK_MICROSOFT_ACTION",
  contextRpcMethod: rpcRoutes.getMicrosoftAuthShortcutContext,
};

/** Log tag. */
function logTag(config: AuthShortcutConfig): string {
  return `[AuthShortcutLaunch:${config.store}]`;
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
 * Generic auth-shortcut launcher used by all stores that
 * capture credentials inside a Wine prefix (Epic, GOG,
 * Amazon, Microsoft). Always creates a fresh ephemeral
 * non-Steam shortcut, asks Steam to launch it through the
 * launcher binary, then removes the shortcut after a 15s
 * grace period so the user's library never accumulates
 * "Sign-In" entries.
 *
 * No persistent path: the previous implementation tried to
 * reuse a backend-written shortcut, but Steam's in-memory
 * app store is only populated from shortcuts.vdf at Steam
 * startup, so backend writes mid-session weren't visible.
 * Whichever stores happened to have their persistent
 * entry loaded by Steam (typically only Epic) launched
 * with the "real game" tile + cover art, while the rest
 * fell through to the temp path. Going temp-only makes
 * every store behave the same.
 */
export async function launchAuthViaShortcut(
  config: AuthShortcutConfig,
): Promise<AuthShortcutLaunchResult> {
  const tag = logTag(config);
  console.log(`${tag} Starting auth shortcut launch flow`);
  const raw = await call<[], unknown>(config.contextRpcMethod);
  const ctx = unwrapRpcEnvelope<AuthShortcutContextRPC>(raw, {
    route: config.contextRpcMethod,
    throwing: false,
  });
  if (!ctx?.launcher_path) {
    console.error(
      `${tag} Auth context failed — envelope:`,
      raw,
      `unwrapped:`,
      ctx,
    );
    return {
      success: false,
      error: ctx?.error || "Auth launcher path unavailable",
    };
  }
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame || !steamApps?.SetShortcutLaunchOptions) {
    return {
      success: false,
      error: "Steam shortcut launch APIs are unavailable",
    };
  }
  const tempStoreId = `${config.tempStoreIdPrefix}-${Date.now()}`;
  const tempLaunchOptions = `${tempStoreId} ${config.actionEnvVar}=auth`;
  const tempAppId = await createTemporaryShortcut({
    appName: config.appName,
    launcherPath: ctx.launcher_path,
    launchOptions: tempLaunchOptions,
    logTag: logTag(config),
  });
  if (tempAppId === null) {
    return {
      success: false,
      error:
        `${config.appName} could not be prepared in Steam. ` +
        `Restart Steam once and try again.`,
    };
  }
  const alreadyRunning = isShortcutAppRunning(tempAppId);
  try {
    steamApps.SpecifyCompatTool?.(tempAppId, "");
    // Best-effort: give the login window a keyboard/mouse layout so the
    // store sign-in page is navigable. Fully guarded — never blocks the
    // launch (see applyWebBrowserLayout).
    applyWebBrowserLayout(tempAppId);
    steamApps.SetShortcutLaunchOptions(tempAppId, tempLaunchOptions);
    const runGameId = getShortcutRunGameId(tempAppId);
    console.log(
      `${tag} Calling RunGame: appId=${tempAppId}, ` +
        `runGameId=${runGameId}, launchOpts="${tempLaunchOptions}"`,
    );
    steamApps.RunGame(runGameId, "", -1, 100);
    scheduleTemporaryShortcutCleanup(tempAppId, logTag(config));
    console.log(`${tag} Auth launched via RunGame (appId=${tempAppId})`);
    return { success: true, already_running: alreadyRunning, appId: tempAppId };
  } catch (error) {
    console.error(`${tag} Shortcut launch failed:`, error);
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
export const launchGogAuthViaShortcut = (): Promise<AuthShortcutLaunchResult> =>
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
