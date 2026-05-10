/**
 * Controller-aware launch wrapper.
 *
 * Programmatic Steam controller-layout editing was removed
 * from this module after observation in production : the
 * automation only proved Steam-side state changes, threw
 * runtime "Unknown method" errors in the Steam UI for some
 * launches, and could race Steam's own controller
 * configurator. Until we have a Game Mode-safe signal, this
 * wrapper only launches the shortcut and leaves controller
 * layouts alone.
 *
 * Standards compliance : the previous version reached into
 * `(window as any).appStore` directly, bypassing the
 * SteamBridge isolation layer. This refactor delegates to
 * `getShortcutRunGameId` which lives inside SteamBridge —
 * any future change to the Steam internal path is then
 * a one-file edit.
 */
import { getShortcutRunGameId } from "../lib/steam-bridge";

const LOG_PREFIX = "[ControllerConfig]";

/** Hook left as a no-op for forward-compatibility : when a
 *  Game-Mode-safe controller config signal lands, this is
 *  where the per-app config orchestration will be invoked. */
export async function ensureGamepadConfigForApp(appId: number): Promise<void> {
  console.log(`${LOG_PREFIX} Skipping automatic controller configuration` + ` for appId=${appId}`);
}

/** Launch a Steam shortcut by appId, going through Steam's
 *  RunGame API. Returns false when Steam's Apps surface is
 *  unavailable (test environments, very early plugin boot). */
export async function launchAppWithConfiguredGamepad(appId: number): Promise<boolean> {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame) {
    return false;
  }

  await ensureGamepadConfigForApp(appId);
  steamApps.RunGame(getShortcutRunGameId(appId), "", -1, 100);
  console.log(`${LOG_PREFIX} Launched appId=${appId} ` + `without changing controller layouts`);

  return true;
}

/** Reserved for the day a one-shot post-launch hand-off is
 *  needed (e.g. to consume a deferred config payload). The
 *  current implementation is a no-op so callers get a stable
 *  API while the underlying signal is being designed. */
export function consumeConfiguredLaunch(_appId: number): boolean {
  return false;
}
/** Reset any in-process caches the controller-config layer
 *  may have accumulated. No-op today; kept for symmetry with
 *  the public surface the legacy module exposed. */
export function resetControllerConfigCache(): void {
  /* intentionally empty */
}
