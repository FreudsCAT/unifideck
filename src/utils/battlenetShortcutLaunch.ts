/**
 * Battle.net auth / install launch via a Steam shortcut.
 *
 * src/utils/battlenetShortcutLaunch.ts
 *
 * The backend must NOT spawn the vendor client itself: in Gaming Mode a
 * backend subprocess has no gamescope session and the window never renders.
 * So the frontend RunGame-s a shortcut instead, exactly as the Ubisoft flow
 * does.
 *
 * Built directly on the shared `steam-bridge/temp-shortcut` primitives
 * rather than on `ubisoftShortcutLaunch`, whose equivalent helper is
 * module-private and closes over Ubisoft-specific constants. Factoring that
 * one out is worthwhile but means editing a shipped auth path that only a
 * real device can exercise, so it is deliberately deferred rather than
 * bundled here.
 *
 * Battle.net also needs less than Ubisoft: no `UNIFIDECK_*_PREFIX_NAME` is
 * threaded, because the launcher resolves the per-game prefix from the
 * recorded id map rather than from a launch-time token.
 */

import { call } from "@decky/api";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import {
  createTemporaryShortcut,
  scheduleTemporaryShortcutCleanup,
} from "../lib/steam-bridge/temp-shortcut";

const LOG_TAG = "[BattlenetShortcutLaunch]";
const AUTH_SHORTCUT_STORE_ID = "battlenet:bnet-auth";
const SHORTCUT_DISPLAY_NAME = "Battle.net";
const DEFAULT_LAUNCH_WAIT_MS = 1500;

export interface BattlenetLaunchResult {
  success: boolean;
  error?: string;
}

interface AuthShortcutContext {
  appid_unsigned?: number;
  launch_wait_ms?: number;
  launcher_path?: string;
  error?: string;
}

async function fetchAuthContext(): Promise<AuthShortcutContext | undefined> {
  const raw = await call<[], unknown>(
    rpcRoutes.getBattlenetAuthShortcutContext,
  ).catch(() => null);
  if (raw == null) return undefined;
  return unwrapRpcEnvelope<AuthShortcutContext>(raw, {
    route: rpcRoutes.getBattlenetAuthShortcutContext,
    throwing: false,
  });
}

function buildLaunchOptions(storeGameId: string, action: string): string {
  return `${storeGameId} UNIFIDECK_BATTLENET_ACTION=${action}`;
}

async function runShortcut(
  appId: number,
  launchWaitMs: number,
): Promise<BattlenetLaunchResult> {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame) {
    return { success: false, error: "Steam launch APIs unavailable" };
  }
  steamApps.RunGame(String(appId), "", -1, 100);
  await new Promise((resolve) => setTimeout(resolve, launchWaitMs));
  return { success: true };
}

async function launchViaTempShortcut(
  storeGameId: string,
  action: string,
  launcherPath: string | undefined,
): Promise<BattlenetLaunchResult> {
  if (!launcherPath) {
    return { success: false, error: "launcher path unavailable" };
  }
  // Steam only reads shortcuts.vdf at startup, so a shortcut written this
  // session is invisible to RunGame. The temporary shortcut is what makes a
  // first-ever sign-in work without asking the user to restart Steam.
  const tempAppId = await createTemporaryShortcut({
    appName: SHORTCUT_DISPLAY_NAME,
    launcherPath,
    launchOptions: buildLaunchOptions(storeGameId, action),
    logTag: LOG_TAG,
  });
  if (tempAppId == null) {
    return { success: false, error: "could not create temporary shortcut" };
  }
  const result = await runShortcut(tempAppId, DEFAULT_LAUNCH_WAIT_MS);
  scheduleTemporaryShortcutCleanup(tempAppId, LOG_TAG);
  return result;
}

/**
 * Open the Battle.net client so the user can sign in.
 *
 * The client login is the PRIMARY credential for this store: it produces
 * both the licence ledger and the cached PUB catalog the library is built
 * from. Until it has happened once, an empty library is correct rather than
 * a failure.
 */
export async function launchBattlenetAuthViaShortcut(): Promise<BattlenetLaunchResult> {
  const ctx = await fetchAuthContext();
  if (ctx?.appid_unsigned) {
    return runShortcut(
      ctx.appid_unsigned,
      ctx.launch_wait_ms ?? DEFAULT_LAUNCH_WAIT_MS,
    );
  }
  console.info(`${LOG_TAG} no persistent auth shortcut — using temp shortcut`);
  return launchViaTempShortcut(
    AUTH_SHORTCUT_STORE_ID,
    "auth",
    ctx?.launcher_path,
  );
}

/**
 * Open the client on a game's page so the user can press Install.
 *
 * `--exec="install <FAMILY>"` does not start a download — measured against
 * the current client with a known-good family code — so the install is a
 * user click inside the client. The download worker owns completion by
 * polling `product.db`.
 */
export async function launchBattlenetInstallViaShortcut(
  storeGameId: string,
): Promise<BattlenetLaunchResult> {
  const ctx = await fetchAuthContext();
  return launchViaTempShortcut(storeGameId, "install", ctx?.launcher_path);
}
