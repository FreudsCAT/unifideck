/**
 * Battle.net shortcut launcher — configuration only.
 *
 * src/utils/battlenetShortcutLaunch.ts
 *
 * The behaviour lives in `lib/steam-bridge/wrapper-shortcut-launch.ts`,
 * shared with Ubisoft Connect (and EA App next). This file is only the
 * values that are actually Battle.net's.
 *
 * `prefixEnvVar` IS threaded, and is load-bearing for sign-in. The
 * launcher resolves a per-*game* prefix from the recorded id map, but the
 * auth prefix has no id-map record — so without this token it falls back
 * to `ctx.game_id` ("bnet-auth") and signs the user in to an empty
 * `prefixes/battlenet/bnet-auth` while the client lives in `.bnet-auth`.
 * Observed on-device: an empty stray prefix created beside the real one.
 * This must stay in step with `BattlenetStore.AUTH_SHORTCUT`, which writes
 * the same token into the persistent shortcut's LaunchOptions.
 */
import { rpcRoutes } from "../api/rpc-routes";
import type { ShortcutLaunchResult } from "../lib/steam-bridge";
import {
  type WrapperShortcutConfig,
  launchWrapperAuthViaShortcut,
  launchWrapperViaShortcut,
  wrapperActionEnv,
} from "../lib/steam-bridge/wrapper-shortcut-launch";

export const BATTLENET_SHORTCUT_CONFIG: WrapperShortcutConfig = {
  storeId: "battlenet",
  displayName: "Battle.net",
  logTag: "[BattlenetShortcutLaunch]",
  authShortcutStoreId: "battlenet:bnet-auth",
  authContextRoute: rpcRoutes.getBattlenetAuthShortcutContext,
  actionEnvVar: "UNIFIDECK_BATTLENET_ACTION",
  prefixEnvVar: "UNIFIDECK_BATTLENET_PREFIX_NAME",
  authPrefixName: ".bnet-auth",
};

/**
 * Open the Battle.net client so the user can sign in.
 *
 * The client login is the PRIMARY credential for this store: it produces
 * both the licence ledger and the cached PUB catalog the library is built
 * from. Until it has happened once, an empty library is correct rather
 * than a failure.
 */
export async function launchBattlenetAuthViaShortcut(): Promise<ShortcutLaunchResult> {
  return launchWrapperAuthViaShortcut(BATTLENET_SHORTCUT_CONFIG);
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
): Promise<ShortcutLaunchResult> {
  return launchWrapperViaShortcut(
    BATTLENET_SHORTCUT_CONFIG,
    storeGameId,
    wrapperActionEnv(BATTLENET_SHORTCUT_CONFIG, "install"),
  );
}
