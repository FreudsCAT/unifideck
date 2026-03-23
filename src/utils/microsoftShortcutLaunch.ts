/**
 * Launch Microsoft auth Chromium via Steam's RunGame API.
 *
 * This mirrors the Ubisoft auth shortcut pattern: a hidden non-Steam
 * shortcut runs unifideck-launcher with UNIFIDECK_MICROSOFT_ACTION=auth,
 * which launches Chromium with the OAuth URL.  Because Steam manages the
 * game session, gamescope surfaces the Chromium window in gaming mode.
 */
import { call } from "@decky/api";
import {
  type ShortcutLaunchContext,
  type ShortcutLaunchResult,
  getShortcutRunGameId,
} from "./ubisoftShortcutLaunch";

const MS_AUTH_SHORTCUT_STORE_ID = "microsoft:ms-auth";

const RESTORE_POLL_DELAY_MS = 250;
const RESTORE_START_DELAY_MS = 500;
const RESTORE_TIMEOUT_MS = 5000;
const SHORTCUT_POLL_DELAY_MS = 250;
const SHORTCUT_POLL_TIMEOUT_MS = 5000;

function isShortcutRegistered(appId: number): boolean {
  const appStore = (window as any).appStore;
  return Boolean(appStore?.m_mapApps?.get?.(appId));
}

async function waitForShortcutRegistration(
  appId: number,
  minimumDelayMs = 0,
): Promise<void> {
  if (minimumDelayMs <= 0 && isShortcutRegistered(appId)) {
    return;
  }

  const startedAt = Date.now();
  const timeoutMs = Math.max(SHORTCUT_POLL_TIMEOUT_MS, minimumDelayMs);
  await new Promise<void>((resolve) => {
    const poll = () => {
      const elapsedMs = Date.now() - startedAt;
      if (
        (elapsedMs >= minimumDelayMs && isShortcutRegistered(appId)) ||
        elapsedMs >= timeoutMs
      ) {
        resolve();
        return;
      }
      window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
    };
    window.setTimeout(poll, SHORTCUT_POLL_DELAY_MS);
  });
}

function scheduleLaunchStateRestore(
  appId: number,
  originalLaunchOptions: string,
): void {
  let restored = false;

  const restore = () => {
    if (restored) return;
    restored = true;
    window.SteamClient?.Apps?.SetShortcutLaunchOptions?.(
      appId,
      originalLaunchOptions,
    );
  };

  const startedAt = Date.now();
  const poll = () => {
    if (Date.now() - startedAt >= RESTORE_TIMEOUT_MS) {
      restore();
      return;
    }
    window.setTimeout(poll, RESTORE_POLL_DELAY_MS);
  };
  window.setTimeout(poll, RESTORE_START_DELAY_MS);
}

export async function launchMicrosoftAuthViaShortcut(): Promise<ShortcutLaunchResult> {
  const authContext = await call<
    [],
    {
      success: boolean;
      appid_unsigned?: number;
      launch_wait_ms?: number;
      error?: string;
    }
  >("get_microsoft_auth_shortcut_context");

  if (!authContext?.success || !authContext.appid_unsigned) {
    return {
      success: false,
      error: authContext?.error || "Auth shortcut not available",
    };
  }

  const appId = authContext.appid_unsigned;
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame || !steamApps?.SetShortcutLaunchOptions) {
    return {
      success: false,
      error: "Steam shortcut launch APIs are unavailable",
    };
  }

  await waitForShortcutRegistration(appId, authContext.launch_wait_ms ?? 0);

  // Build temporary launch options with auth action marker.
  // The launcher parses UNIFIDECK_MICROSOFT_ACTION=auth and reads the
  // OAuth URL from ~/.local/share/unifideck/ms_auth_url.txt.
  const temporaryLaunchOptions =
    `${MS_AUTH_SHORTCUT_STORE_ID} UNIFIDECK_MICROSOFT_ACTION=auth`;

  // Fetch current launch options to restore after launch
  const shortcutContext = await call<[string], ShortcutLaunchContext>(
    "get_compat_tool_for_game",
    MS_AUTH_SHORTCUT_STORE_ID,
  ).catch(() => ({ success: false } as ShortcutLaunchContext));

  const originalLaunchOptions =
    shortcutContext.current_launch_options ?? MS_AUTH_SHORTCUT_STORE_ID;

  try {
    // Auth shortcut is native (no Proton) -- clear any compat tool
    steamApps.SpecifyCompatTool?.(appId, "");

    steamApps.SetShortcutLaunchOptions(appId, temporaryLaunchOptions);
    steamApps.RunGame(getShortcutRunGameId(appId), "", -1, 100);
    scheduleLaunchStateRestore(appId, originalLaunchOptions);

    console.log(
      `[MicrosoftShortcutLaunch] Auth launched via RunGame (appId=${appId})`,
    );
    return { success: true };
  } catch (error) {
    console.error("[MicrosoftShortcutLaunch] Shortcut launch failed:", error);
    steamApps.SetShortcutLaunchOptions?.(appId, originalLaunchOptions);
    return {
      success: false,
      error:
        error instanceof Error ? error.message : "Failed to launch shortcut",
    };
  }
}
