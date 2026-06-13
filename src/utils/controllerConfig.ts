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
 * keyboard/mouse layout, so `applyWebBrowserLayout` below ships a
 * fully-guarded best-effort: it probe-logs the real
 * `SteamClient.Input` method names (so the exact setter can be
 * confirmed on-device from the CEF console) and tries a few candidate
 * setters. Every call is wrapped so a wrong/absent method silently
 * no-ops and can NEVER break the auth launch — it is applied AFTER the
 * temp shortcut's app entry exists and BEFORE `RunGame`. Once a setter
 * is verified on-device, prune the candidate list to the confirmed one.
 */
import { getShortcutRunGameId } from "../lib/steam-bridge";

const LOG_PREFIX = "[ControllerConfig]";

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

/**
 * Best-effort: apply a keyboard/mouse ("Web Browser") controller
 * layout to the temporary auth-window shortcut so the store-login page
 * is navigable with the trackpad/stick instead of a useless gamepad
 * binding.
 *
 * The exact `SteamClient.Input` setter varies by Steam build and isn't
 * in `@decky`'s typings, so this is deliberately defensive:
 *
 *   1. It logs the available `SteamClient.Input` method names — read
 *      these in the CEF console to confirm the right call, then this
 *      list can be pruned to the verified one.
 *   2. It tries each candidate setter, individually guarded, and stops
 *      at the first that doesn't throw.
 *
 * Every path is wrapped: a missing/renamed method, or any thrown error,
 * leaves the auth launch completely unaffected (the login still works,
 * just with the default layout). Call this AFTER the shortcut's app
 * entry exists and BEFORE `RunGame`.
 */
export function applyWebBrowserLayout(appId: number): void {
  try {
    const input = (
      window.SteamClient as unknown as {
        Input?: Record<string, unknown>;
      }
    )?.Input;
    if (!input) {
      console.log(
        `${LOG_PREFIX} SteamClient.Input unavailable — ` +
          `leaving default layout for appId=${appId}`,
      );
      return;
    }
    // Probe: surface the real method names for on-device verification.
    console.log(
      `${LOG_PREFIX} SteamClient.Input methods:`,
      Object.keys(input).sort(),
    );

    // Candidate keyboard/mouse-template setters, most-specific first.
    // Each is guarded so a wrong/absent name never breaks auth.
    const candidates: Array<
      [string, (fn: (...a: unknown[]) => unknown) => unknown]
    > = [
      ["SetWebBrowserActiveControllerConfig", (fn) => fn(appId)],
      ["SetControllerConfigForApp", (fn) => fn(appId, "web_browser")],
      ["SetActiveControllerConfiguration", (fn) => fn(appId, "web_browser")],
      ["SetControllerToTemplateBindings", (fn) => fn(appId, "web_browser")],
    ];
    for (const [name, invoke] of candidates) {
      const fn = input[name];
      if (typeof fn !== "function") continue;
      try {
        invoke(fn.bind(input) as (...a: unknown[]) => unknown);
        console.log(
          `${LOG_PREFIX} applied web-browser layout via ` +
            `${name}(appId=${appId})`,
        );
        return;
      } catch (e) {
        console.warn(`${LOG_PREFIX} ${name} failed (trying next):`, e);
      }
    }
    console.log(
      `${LOG_PREFIX} no known controller-config setter matched — ` +
        `default layout kept for appId=${appId}`,
    );
  } catch (e) {
    console.warn(`${LOG_PREFIX} applyWebBrowserLayout error (ignored):`, e);
  }
}
