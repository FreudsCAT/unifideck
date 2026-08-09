/**
 * Ubisoft Connect shortcut launcher — configuration only.
 *
 * src/utils/ubisoftShortcutLaunch.ts
 *
 * The behaviour lives in `lib/steam-bridge/wrapper-shortcut-launch.ts`,
 * shared with every other wrapper store (Battle.net now, EA App next).
 * This file is just the handful of values that are actually Ubisoft's.
 *
 * Ubisoft is the one store that threads `UNIFIDECK_UBISOFT_PREFIX_NAME`:
 * its auth prefix is selected at launch time. Battle.net does not, because
 * its launcher resolves the per-game prefix from the recorded id map.
 */
import { rpcRoutes } from "../api/rpc-routes";
import type {
  ShortcutLaunchContext,
  ShortcutLaunchResult,
} from "../lib/steam-bridge";
import {
  type WrapperShortcutConfig,
  launchWrapperAuthViaShortcut,
  launchWrapperViaShortcut,
  terminateShortcutApp as terminateWrapperShortcutApp,
} from "../lib/steam-bridge/wrapper-shortcut-launch";

const LOG_TAG = "[UbisoftShortcutLaunch]";

export const UBISOFT_SHORTCUT_CONFIG: WrapperShortcutConfig = {
  storeId: "ubisoft",
  displayName: "Ubisoft Connect",
  logTag: LOG_TAG,
  authShortcutStoreId: "ubisoft:upc-auth",
  authContextRoute: rpcRoutes.getUbisoftAuthShortcutContext,
  actionEnvVar: "UNIFIDECK_UBISOFT_ACTION",
  prefixEnvVar: "UNIFIDECK_UBISOFT_PREFIX_NAME",
  authPrefixName: ".upc-auth",
};

/** Force-stop a shortcut launch via Steam's TerminateApp. */
export function terminateShortcutApp(appId: number): boolean {
  return terminateWrapperShortcutApp(appId, LOG_TAG);
}

/**
 * Launch a Ubisoft game via its existing shortcut, passing the install_id
 * so the launcher knows which UPC entry to start.
 */
export async function launchUbisoftInstallViaShortcut(
  storeGameId: string,
  extraEnv: Record<string, string> = {},
  contextOverride?: Partial<ShortcutLaunchContext>,
  skipStateRestore = false,
): Promise<ShortcutLaunchResult> {
  return launchWrapperViaShortcut(
    UBISOFT_SHORTCUT_CONFIG,
    storeGameId,
    extraEnv,
    contextOverride,
    skipStateRestore,
  );
}

/** Launch the Ubisoft auth flow via its dedicated auth shortcut. */
export async function launchUbisoftAuthViaShortcut(): Promise<ShortcutLaunchResult> {
  return launchWrapperAuthViaShortcut(UBISOFT_SHORTCUT_CONFIG);
}
