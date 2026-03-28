/**
 * Microsoft/xCloud launch wrapper.
 *
 * We intentionally avoid programmatic Steam controller-layout editing here.
 * The previous automation only proved Steam-side state changes, threw runtime
 * "Unknown method" errors in the Steam UI for some launches, and could race
 * Steam's own controller configurator. Until we have a Game Mode-safe signal,
 * this wrapper only launches the shortcut and leaves controller layouts alone.
 */

const LOG_PREFIX = "[ControllerConfig]";

function getRunGameId(appId: number): string {
  const appStore = (window as any).appStore;
  const app = appStore?.m_mapApps?.get?.(appId);
  const gameId = app?.gameid;
  return typeof gameId === "string" && gameId.length > 0
    ? gameId
    : String(appId);
}

export async function ensureGamepadConfigForApp(appId: number): Promise<void> {
  console.log(
    `${LOG_PREFIX} Skipping automatic controller configuration for appId=${appId}`,
  );
}

export async function launchAppWithConfiguredGamepad(
  appId: number,
): Promise<boolean> {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame) {
    return false;
  }

  await ensureGamepadConfigForApp(appId);
  steamApps.RunGame(getRunGameId(appId), "", -1, 100);
  console.log(
    `${LOG_PREFIX} Launched appId=${appId} without changing controller layouts`,
  );
  return true;
}

export function consumeConfiguredLaunch(_appId: number): boolean {
  return false;
}

export function resetControllerConfigCache(): void {}
