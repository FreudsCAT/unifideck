/**
 * Ubisoft shortcut launcher.
 *
 * Kept separate from the generic `authShortcutLaunch.ts`
 * because Ubisoft's auth flow is genuinely different :
 *
 *  - Reuses the existing Ubisoft Connect shortcut instead of
 *    creating a temporary one.
 *  - Saves and restores the user's proton tool after the
 *    auth run (so changing compat tool for the auth flow
 *    doesn't leak into the main game launch).
 *  - Extracts and re-injects user-supplied launch parameters
 *    around the auth env var so #%command% wrappers (mangohud,
 *    gamemoderun, etc.) are preserved.
 *
 * This file imports its shared types and helpers from
 * `lib/steam-bridge` (OP-F02e). The legacy version had its
 * own duplicated copies of those primitives; the unified
 * shortcut-types module made them centrally consumable.
 */
import { call } from "@decky/api";
import {
  type ShortcutLaunchContext,
  type ShortcutLaunchResult,
  createTemporaryShortcut,
  getShortcutRunGameId,
  isShortcutAppRunning,
  scheduleTemporaryShortcutCleanup,
} from "../lib/steam-bridge";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";

const RESTORE_POLL_DELAY_MS = 250;
const RESTORE_START_DELAY_MS = 500;
const RESTORE_TIMEOUT_MS = 5000;
const SHORTCUT_REGISTRATION_POLL_DELAY_MS = 250;
const AUTH_SHORTCUT_STORE_ID = "ubisoft:upc-auth";
const AUTH_PREFIX_NAME = ".upc-auth";
const LOG_TAG = "[UbisoftShortcutLaunch]";
const SHORTCUT_DISPLAY_NAME = "Ubisoft Connect";

/** Escape reg exp. */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Strip Unifideck env tokens, the store_game_id, and the
 *  launcher path from the user's launch_options string so we
 *  keep only the user-supplied wrappers (mangohud, gamemoderun,
 *  #%command%, etc.). */
function extractUserParams(
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

/** Build temporary launch options. */
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

/** App store entry. */
interface AppStoreEntry {
  display_name?: unknown;
}

/** App store shape. */
interface AppStoreShape {
  m_mapApps?: { get?: (id: number) => AppStoreEntry | undefined };
}

/** App store. */
function appStore(): AppStoreShape | undefined {
  return (window as unknown as { appStore?: AppStoreShape }).appStore;
}

/** Check whether shortcut registered. */
function isShortcutRegistered(appId: number): boolean {
  return Boolean(appStore()?.m_mapApps?.get?.(appId));
}

/** Wait (at most ``minimumDelayMs``) for Steam to register the
 *  persistent shortcut in its in-memory app store.
 *
 *  Steam only loads ``shortcuts.vdf`` into ``appStore.m_mapApps`` at
 *  startup, so a shortcut the backend just wrote this session never
 *  appears here — there's no point polling a fixed timeout. We honour
 *  only the backend's ``minimumDelayMs`` hint (to cover the narrow
 *  window where Steam is still populating the map right after boot);
 *  if it's still not registered the caller falls back to a temporary
 *  shortcut created via ``AddShortcut``. */
async function waitForShortcutRegistration(
  appId: number,
  minimumDelayMs = 0,
): Promise<void> {
  if (isShortcutRegistered(appId)) return;
  if (minimumDelayMs <= 0) return;
  const startedAt = Date.now();
  await new Promise<void>((resolve) => {
    /** Poll. */
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

/** Launch a Ubisoft action (auth or install) via a freshly-created
 *  throwaway shortcut. Used when the persistent "Ubisoft Connect"
 *  shortcut isn't yet in Steam's in-memory app store — the first
 *  session after the backend wrote it to ``shortcuts.vdf`` (Steam only
 *  loads that file at startup). ``RunGame`` on the unregistered
 *  persistent appid fails with Steam's "Game configuration unavailable"
 *  modal; ``AddShortcut`` registers an entry immediately and returns a
 *  real ``gameid``. The temp shortcut carries the same launch options,
 *  so the launcher routes identically, and is removed once its session
 *  ends. */
async function launchViaTemporaryShortcut(
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
    appName: SHORTCUT_DISPLAY_NAME,
    launcherPath,
    launchOptions,
    logTag: LOG_TAG,
  });
  if (tempAppId === null) {
    return {
      success: false,
      error:
        "Ubisoft Connect could not be prepared in Steam. " +
        "Restart Steam once and try again.",
    };
  }
  const alreadyRunning = isShortcutAppRunning(tempAppId);
  try {
    steamApps.SpecifyCompatTool?.(tempAppId, ctx.tool_name ?? "");
    steamApps.SetShortcutLaunchOptions?.(tempAppId, launchOptions);
    steamApps.RunGame(getShortcutRunGameId(tempAppId), "", -1, 100);
    scheduleTemporaryShortcutCleanup(tempAppId, LOG_TAG);
    return { success: true, already_running: alreadyRunning };
  } catch (error) {
    console.error(`${LOG_TAG} temp shortcut launch failed:`, error);
    return {
      success: false,
      error:
        error instanceof Error ? error.message : "Failed to launch shortcut",
    };
  }
}

/** Force-stop a shortcut launch via Steam's TerminateApp. */
export function terminateShortcutApp(appId: number): boolean {
  try {
    window.SteamClient?.Apps?.TerminateApp?.(
      getShortcutRunGameId(appId),
      false,
    );
    return true;
  } catch (error) {
    console.error(
      `[UbisoftShortcutLaunch] terminateShortcutApp failed for ${appId}:`,
      error,
    );
    return false;
  }
}

/** Schedule a post-launch restore : after Steam picks up the
 *  RunGame call we restore the user's saved compat tool and
 *  the original launch options. The restore polls until Steam
 *  reports the app as running, then waits a small grace
 *  period to avoid clobbering a still-applying RunGame. */
function scheduleLaunchStateRestore(
  appId: number,
  context: ShortcutLaunchContext,
  originalLaunchOptions: string,
): void {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps) return;

  const startedAt = Date.now();
  const targetTool = context.saved_proton_tool ?? "";

  const tryRestore = (): void => {
    /** Try restore. */
    const elapsed = Date.now() - startedAt;
    if (elapsed < RESTORE_START_DELAY_MS) {
      window.setTimeout(tryRestore, RESTORE_POLL_DELAY_MS);
      return;
    }
    const running = isShortcutAppRunning(appId);
    if (!running && elapsed < RESTORE_TIMEOUT_MS) {
      window.setTimeout(tryRestore, RESTORE_POLL_DELAY_MS);
      return;
    }
    try {
      steamApps.SpecifyCompatTool?.(appId, targetTool);
      steamApps.SetShortcutLaunchOptions?.(appId, originalLaunchOptions);
    } catch (error) {
      console.error(
        `[UbisoftShortcutLaunch] Restore failed for appId=${appId}:`,
        error,
      );
    }
  };
  window.setTimeout(tryRestore, RESTORE_START_DELAY_MS);
}

/** Launch a Ubisoft GAME via its existing shortcut, passing
 *  the install_id so the launcher knows which UPC entry to
 *  start. */
export async function launchUbisoftInstallViaShortcut(
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
  // ``contextOverride`` wins. The auth flow resolves the shortcut's
  // appid via ``get_ubisoft_auth_shortcut_context`` (which ensures the
  // shortcut exists and repairs the VDF); ``get_compat_tool_for_game``
  // only reads an already-registered game's compat setting and returns
  // no appid for the auth shortcut — hence the "Context unavailable"
  // regression when the auth path relied on it alone.
  const ctx = { ...baseCtx, ...contextOverride } as ShortcutLaunchContext;
  console.log("[UbisoftShortcutLaunch] getCompatToolForGame raw:", rawCtx);
  console.log("[UbisoftShortcutLaunch] resolved ctx:", ctx);

  // The RPC envelope strips ``success`` from the data dict
  // (``_to_envelope`` moves it to the outer layer). Check
  // ``appid_unsigned`` directly — if the backend returned a
  // valid AppID the call succeeded regardless of whether a
  // ``success`` key survived the envelope unwrapping.
  if (!ctx.appid_unsigned) {
    console.error(
      "[UbisoftShortcutLaunch] ctx.appid_unsigned is falsy:",
      ctx.appid_unsigned,
      "full ctx:",
      ctx,
    );
    return { success: false, error: ctx?.error || "Context unavailable" };
  }

  const appId = ctx.appid_unsigned;
  console.log(
    "[UbisoftShortcutLaunch] appId=%d, waiting for shortcut registration...",
    appId,
  );
  await waitForShortcutRegistration(appId, ctx.launch_wait_ms ?? 0);
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame || !steamApps?.SetShortcutLaunchOptions) {
    console.error(
      "[UbisoftShortcutLaunch] Steam launch APIs unavailable: " +
        "RunGame=%s SetShortcutLaunchOptions=%s",
      typeof steamApps?.RunGame,
      typeof steamApps?.SetShortcutLaunchOptions,
    );
    return { success: false, error: "Steam launch APIs unavailable" };
  }

  const originalOptions = ctx.current_launch_options ?? "";
  const tempOptions = buildTemporaryLaunchOptions(ctx, extraEnv, storeGameId);

  // First session after the backend wrote this shortcut to shortcuts.vdf:
  // Steam only loads that file into its in-memory app store at startup, so
  // RunGame on the persistent appid fails with "Game configuration
  // unavailable". Launch a throwaway shortcut instead (AddShortcut registers
  // it immediately); Steam picks up the persistent entry on the next restart,
  // after which the registered path below is used.
  if (!isShortcutRegistered(appId)) {
    console.log(
      "[UbisoftShortcutLaunch] appId=%d not in Steam's app store; " +
        "launching via temporary shortcut",
      appId,
    );
    return launchViaTemporaryShortcut(ctx, tempOptions);
  }

  const alreadyRunning = isShortcutAppRunning(appId);
  console.log(
    "[UbisoftShortcutLaunch] RunGame(appId=%d, runGameId=%s, opts=%s)",
    appId,
    getShortcutRunGameId(appId),
    tempOptions,
  );

  try {
    steamApps.SpecifyCompatTool?.(appId, ctx.tool_name ?? "");
    steamApps.SetShortcutLaunchOptions(appId, tempOptions);
    steamApps.RunGame(getShortcutRunGameId(appId), "", -1, 100);
    console.log("[UbisoftShortcutLaunch] RunGame called successfully");
    // The auth shortcut's temp options ARE its canonical persistent
    // options ("ubisoft:upc-auth UNIFIDECK_UBISOFT_ACTION=auth ..."), so
    // we must NOT restore them to the (empty) original — doing so wiped
    // the shortcut's LaunchOptions, leaving Steam to launch the bare
    // launcher with no args ("missing store:game_id" → the tile that
    // opens then instantly closes). Game installs still restore so the
    // user's wrappers / clean options come back.
    if (!skipStateRestore) {
      scheduleLaunchStateRestore(appId, ctx, originalOptions);
    }

    return { success: true, already_running: alreadyRunning };
  } catch (error) {
    console.error(`[UbisoftShortcutLaunch] launch failed:`, error);
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

/** Launch the Ubisoft AUTH flow via a dedicated auth shortcut
 *  (separate prefix to keep auth tokens away from the game
 *  prefix). The flow is otherwise the same as install. */
export async function launchUbisoftAuthViaShortcut(): Promise<ShortcutLaunchResult> {
  // Resolve (and ensure) the persistent auth shortcut's appid via the
  // dedicated context route — it scans/repairs the VDF and creates the
  // shortcut if missing. get_compat_tool_for_game (used by the install
  // path) cannot see the auth shortcut, so relying on it alone yielded
  // "Context unavailable". Mirrors staging's auth flow.
  const rawAuth = await call<[], unknown>(
    rpcRoutes.getUbisoftAuthShortcutContext,
  ).catch(() => null);
  const authCtx =
    rawAuth == null
      ? undefined
      : unwrapRpcEnvelope<{
          appid_unsigned?: number;
          launch_wait_ms?: number;
          launcher_path?: string;
          error?: string;
        }>(rawAuth, {
          route: rpcRoutes.getUbisoftAuthShortcutContext,
          throwing: false,
        });
  if (!authCtx?.appid_unsigned) {
    console.error(
      "[UbisoftShortcutLaunch] auth shortcut context unavailable:",
      authCtx,
    );
    return {
      success: false,
      error: authCtx?.error || "Auth shortcut not available",
    };
  }
  // Re-add UNIFIDECK_UBISOFT_ACTION=auth explicitly: buildTemporaryLaunchOptions
  // strips all UNIFIDECK_* tokens from the shortcut's stored options, so the
  // auth action must be re-supplied here or the launcher treats the run as a
  // game launch instead of a sign-in.
  return launchUbisoftInstallViaShortcut(
    AUTH_SHORTCUT_STORE_ID,
    {
      UNIFIDECK_UBISOFT_ACTION: "auth",
      UNIFIDECK_UBISOFT_PREFIX_NAME: AUTH_PREFIX_NAME,
    },
    {
      appid_unsigned: authCtx.appid_unsigned,
      launch_wait_ms: authCtx.launch_wait_ms,
      launcher_path: authCtx.launcher_path,
    },
    // skipStateRestore: keep the canonical auth LaunchOptions on the
    // shortcut instead of wiping them back to empty after launch.
    true,
  );
}
