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
  getShortcutRunGameId,
  isShortcutAppRunning,
} from "../lib/steam-bridge";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";

const RESTORE_POLL_DELAY_MS = 250;
const RESTORE_START_DELAY_MS = 500;
const RESTORE_TIMEOUT_MS = 5000;
const SHORTCUT_REGISTRATION_POLL_DELAY_MS = 250;
const SHORTCUT_REGISTRATION_TIMEOUT_MS = 5000;
const AUTH_SHORTCUT_STORE_ID = "ubisoft:upc-auth";
const AUTH_PREFIX_NAME = ".upc-auth";

/** Escape reg exp. */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Strip Unifideck env tokens, the store_game_id, and the
 *  launcher path from the user's launch_options string so we
 *  keep only the user-supplied wrappers (mangohud, gamemoderun,
 *  #%command%, etc.). */
function extractUserParams(launchOptions: string, storeGameId: string, launcherPath?: string): string {
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
function buildTemporaryLaunchOptions(context: ShortcutLaunchContext, extraEnv: Record<string, string>,
  launchStoreGameId?: string): string {
  const sourceStoreGameId = context.store_game_id ?? "";
  const storeGameId = launchStoreGameId ?? sourceStoreGameId;
  const currentOptions = context.current_launch_options ?? sourceStoreGameId;
  const userParams = extractUserParams(currentOptions, sourceStoreGameId, context.launcher_path);
  const envTokens = Object.entries(extraEnv)
    .filter(([, value]) => Boolean(value))
    .map(([key, value]) => `${key}=${value}`)
    .join(" ");

    return [storeGameId, envTokens, userParams]
    .filter(Boolean)
    .join(" ")
    .trim();
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

/** Wait for Steam to register a shortcut after the backend
 *  reports it has been written to shortcuts.vdf. */
async function waitForShortcutRegistration(appId: number, minimumDelayMs = 0): Promise<void> {
  if (minimumDelayMs <= 0 && isShortcutRegistered(appId)) return;
  const startedAt = Date.now();
  const timeoutMs = Math.max(SHORTCUT_REGISTRATION_TIMEOUT_MS, minimumDelayMs);
  await new Promise<void>((resolve) => {
    /** Poll. */
    const poll = (): void => {
      const elapsed = Date.now() - startedAt;
      if (elapsed >= minimumDelayMs && isShortcutRegistered(appId)) {
        resolve();
        return;
      }
      if (elapsed >= timeoutMs) {
        resolve();
        return;
      }
      window.setTimeout(poll, SHORTCUT_REGISTRATION_POLL_DELAY_MS);
    };
    window.setTimeout(poll, SHORTCUT_REGISTRATION_POLL_DELAY_MS);
  });
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
function scheduleLaunchStateRestore(appId: number, context: ShortcutLaunchContext, originalLaunchOptions: string): void {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps) return;

  const startedAt = Date.now();
  const targetTool = context.saved_proton_tool ?? "";

  const tryRestore = (): void => { /** Try restore. */
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
      console.error(`[UbisoftShortcutLaunch] Restore failed for appId=${appId}:`, error);
    }
  };
  window.setTimeout(tryRestore, RESTORE_START_DELAY_MS);
}

/** Launch a Ubisoft GAME via its existing shortcut, passing
 *  the install_id so the launcher knows which UPC entry to
 *  start. */
export async function launchUbisoftInstallViaShortcut(storeGameId: string, extraEnv: Record<string, string> = {}): Promise<ShortcutLaunchResult> {
  const rawCtx = await call<[string], unknown>(
    rpcRoutes.getCompatToolForGame,
    storeGameId,
  );
  const ctx = unwrapRpcEnvelope<ShortcutLaunchContext>(rawCtx, {
    route: rpcRoutes.getCompatToolForGame, throwing: false,
  });
  console.log(
    "[UbisoftShortcutLaunch] getCompatToolForGame raw:", rawCtx,
  );
  console.log(
    "[UbisoftShortcutLaunch] getCompatToolForGame ctx:", ctx,
  );

  // The RPC envelope strips ``success`` from the data dict
  // (``_to_envelope`` moves it to the outer layer). Check
  // ``appid_unsigned`` directly — if the backend returned a
  // valid AppID the call succeeded regardless of whether a
  // ``success`` key survived the envelope unwrapping.
  if (!ctx.appid_unsigned) {
    console.error(
      "[UbisoftShortcutLaunch] ctx.appid_unsigned is falsy:",
      ctx.appid_unsigned, "full ctx:", ctx,
    );
    return { success: false, error: ctx?.error || "Context unavailable" };
  }

  const appId = ctx.appid_unsigned;
  console.log(
    "[UbisoftShortcutLaunch] appId=%d, waiting for shortcut registration...",
    appId,
  );
  await waitForShortcutRegistration(appId, ctx.launch_wait_ms ?? 0);
  console.log(
    "[UbisoftShortcutLaunch] shortcut registered, checking Steam APIs...",
  );
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame || !steamApps?.SetShortcutLaunchOptions) {
    console.error(
      "[UbisoftShortcutLaunch] Steam launch APIs unavailable: "
      + "RunGame=%s SetShortcutLaunchOptions=%s",
      typeof steamApps?.RunGame,
      typeof steamApps?.SetShortcutLaunchOptions,
    );
    return { success: false, error: "Steam launch APIs unavailable" };
  }

  const alreadyRunning = isShortcutAppRunning(appId);
  const originalOptions = ctx.current_launch_options ?? "";
  const tempOptions = buildTemporaryLaunchOptions(ctx, extraEnv, storeGameId);
  console.log(
    "[UbisoftShortcutLaunch] RunGame(appId=%d, runGameId=%s, opts=%s)",
    appId, getShortcutRunGameId(appId), tempOptions,
  );

  try {
    steamApps.SpecifyCompatTool?.(appId, ctx.tool_name ?? "");
    steamApps.SetShortcutLaunchOptions(appId, tempOptions);
    steamApps.RunGame(getShortcutRunGameId(appId), "", -1, 100);
    console.log("[UbisoftShortcutLaunch] RunGame called successfully");
    scheduleLaunchStateRestore(appId, ctx, originalOptions);

    return { success: true, already_running: alreadyRunning };
  } catch (error) {
    console.error(`[UbisoftShortcutLaunch] launch failed:`, error);
    steamApps.SetShortcutLaunchOptions?.(appId, originalOptions);
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
  return launchUbisoftInstallViaShortcut(AUTH_SHORTCUT_STORE_ID, {
    UNIFIDECK_UBISOFT_PREFIX_NAME: AUTH_PREFIX_NAME,
  });
}
