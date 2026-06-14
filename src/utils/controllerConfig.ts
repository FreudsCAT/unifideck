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
 *
 * ── Investigation (2026-06, beta feedback) ──────────────────
 * Beta testers asked for the Unifideck-Launcher / auth-window
 * shortcut to come up with a "Web Browser" controller layout
 * (keyboard/mouse-like bindings) so the in-prefix login UI is
 * navigable. Re-checked the available surface:
 *
 *   • `@decky/ui` ships NO typed `SteamClient.Input.*` surface,
 *     and our `types/steam.ts` declares only `Apps` +
 *     `GameSessions`. The vestigial `ControllerConfigInfo*`
 *     types there describe the read-only template-list *stream*
 *     — there is no verified setter binding.
 *   • The only controller method on the typed `Apps` surface is
 *     `ShowControllerConfigurator(appId)`, which pops Steam's
 *     full configurator UI — too intrusive to fire mid-auth.
 *   • The historical programmatic path (set-active-config /
 *     template apply) is the exact code that raced Steam and
 *     threw "Unknown method"; it was deliberately reverted.
 *
 * Conclusion: not shippable blind for the *game* launch path. But the
 * auth-window path (Edge browser login) genuinely benefits from a
 * keyboard/mouse layout, so `applyWebBrowserLayout` below applies the
 * official Steam "Web Browser" template to the auth shortcut. The API
 * (method names + the `SetSelectedConfigForApp` signature) was verified
 * against the Steam client UI bundle (`steamui/*.js`) and the template
 * itself (`controller_base/templates/controller_neptune_webbrowser.vdf`,
 * Title "Web Browser", official Valve config). Rather than hardcode the
 * template URL (its exact string is produced at runtime), we enumerate
 * the live config list via `QueryControllerConfigsForApp` and pick the
 * official Web-Browser entry. Every call is wrapped so any failure
 * leaves the auth launch completely unaffected; applied AFTER the temp
 * shortcut's app entry exists and BEFORE `RunGame`.
 */
import { getShortcutRunGameId } from "../lib/steam-bridge";
import type {
  ControllerConfigInfoMessage,
  ControllerConfigInfoMessageList,
} from "../types/steam";

const LOG_PREFIX = "[ControllerConfig]";
// The Deck's built-in controller is index 0 in Gaming Mode.
const PRIMARY_CONTROLLER_INDEX = 0;
// How long to wait for Steam to stream the template list before giving up.
const CONFIG_INFO_TIMEOUT_MS = 4000;

/** Hook left as a no-op for forward-compatibility : when a
 *  Game-Mode-safe controller config signal lands, this is
 *  where the per-app config orchestration will be invoked. */
export async function ensureGamepadConfigForApp(appId: number): Promise<void> {
  console.log(
    `${LOG_PREFIX} Skipping automatic controller configuration` +
      ` for appId=${appId}`,
  );
}

/** Launch a Steam shortcut by appId, going through Steam's
 *  RunGame API. Returns false when Steam's Apps surface is
 *  unavailable (test environments, very early plugin boot). */
export async function launchAppWithConfiguredGamepad(
  appId: number,
): Promise<boolean> {
  const steamApps = window.SteamClient?.Apps;
  if (!steamApps?.RunGame) {
    return false;
  }

  await ensureGamepadConfigForApp(appId);
  steamApps.RunGame(getShortcutRunGameId(appId), "", -1, 100);
  console.log(
    `${LOG_PREFIX} Launched appId=${appId} ` +
      `without changing controller layouts`,
  );

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

/** A config-info message that carries a template (vs. a "Done" marker). */
function isTemplateEntry(
  m: ControllerConfigInfoMessage,
): m is ControllerConfigInfoMessageList {
  return (
    "URL" in m && typeof (m as ControllerConfigInfoMessageList).URL === "string"
  );
}

/** The official Steam "Web Browser" template (by title or filename). */
function isWebBrowserTemplate(m: ControllerConfigInfoMessageList): boolean {
  return (
    m.bOfficial && (m.Title === "Web Browser" || /webbrowser/i.test(m.URL))
  );
}

/**
 * Apply the official Steam "Web Browser" controller template to the
 * temporary auth-window shortcut so the store-login page is navigable
 * with the trackpad/stick (mouse) instead of a useless gamepad binding.
 *
 * Flow (all verified against the Steam UI bundle):
 *   1. Register for the app's controller-config info stream.
 *   2. `QueryControllerConfigsForApp` to make Steam emit it.
 *   3. Pick the official Web-Browser entry and apply its `URL` via
 *      `SetSelectedConfigForApp` (which persists the selection).
 *
 * Fully guarded and non-blocking: a missing API, no match, or any
 * thrown error leaves the auth launch completely unaffected (login
 * still works, just with the default layout). A timeout unregisters the
 * listener if Steam never streams a match. Call this AFTER the
 * shortcut's app entry exists and BEFORE `RunGame`.
 */
export function applyWebBrowserLayout(appId: number): void {
  try {
    const input = window.SteamClient?.Input;
    if (
      !input?.RegisterForControllerConfigInfoMessages ||
      !input?.QueryControllerConfigsForApp ||
      !input?.SetSelectedConfigForApp
    ) {
      console.log(
        `${LOG_PREFIX} SteamClient.Input config API unavailable — ` +
          `default layout kept for appId=${appId}`,
      );
      return;
    }
    const idx = PRIMARY_CONTROLLER_INDEX;
    // Mutable holder so `cleanup` can close over the registration +
    // timer that are created after it (avoids a let/const cycle).
    const state: {
      settled: boolean;
      reg?: { unregister(): void };
      timer?: ReturnType<typeof setTimeout>;
    } = { settled: false };
    const cleanup = () => {
      if (state.timer !== undefined) clearTimeout(state.timer);
      try {
        state.reg?.unregister();
      } catch {
        /* ignore */
      }
    };

    state.reg = input.RegisterForControllerConfigInfoMessages(
      appId,
      (messages) => {
        if (state.settled || !Array.isArray(messages)) return;
        const tpl = messages.filter(isTemplateEntry).find(isWebBrowserTemplate);
        if (!tpl) return;
        state.settled = true;
        try {
          input.SetSelectedConfigForApp(appId, idx, tpl.URL, false, true);
          console.log(
            `${LOG_PREFIX} applied Web Browser layout to ` +
              `appId=${appId} (${tpl.URL})`,
          );
        } catch (e) {
          console.warn(`${LOG_PREFIX} SetSelectedConfigForApp failed:`, e);
        }
        cleanup();
      },
    );
    input.QueryControllerConfigsForApp(appId, idx, false);
    state.timer = setTimeout(() => {
      if (!state.settled) {
        console.log(
          `${LOG_PREFIX} Web Browser template not found for ` +
            `appId=${appId} within ${CONFIG_INFO_TIMEOUT_MS}ms — ` +
            `default layout kept`,
        );
      }
      cleanup();
    }, CONFIG_INFO_TIMEOUT_MS);
  } catch (e) {
    console.warn(`${LOG_PREFIX} applyWebBrowserLayout error (ignored):`, e);
  }
}
