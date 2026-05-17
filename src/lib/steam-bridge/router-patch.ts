/**
 * Router patch lifecycle.
 *
 * Wraps `@decky/api`'s `routerHook.addPatch` with a typed
 * handle. Plugins must keep a reference to the handle and
 * call `.remove()` in their unload path; otherwise patches
 * leak across plugin reloads and accumulate.
 */
import { routerHook } from "@decky/api";
import type { RoutePatch } from "@decky/api/dist/types";

/**
 * Handle returned by `patchRouter`. Its `dispose()` must
 * be called on plugin teardown to restore the original
 * router behaviour ; otherwise the patch leaks across
 * dev reloads.
 */
export interface RouterPatchHandle {
  remove(): void;
}

/** Patch a router route. The `patch` function receives the
 *  rendered React node for the route and must return a
 *  (possibly modified) node. */
export function addRouterPatch(path: string, patch: (node: unknown) => unknown): RouterPatchHandle {
  let removed = false;
  let token: RoutePatch | undefined;

  try {
    // addPatch returns a RoutePatch token that must be passed
    // back to removePatch to undo — not an unpatch function.
    token = routerHook.addPatch(path, patch as unknown as RoutePatch);
  } catch (e) {
    console.error("[SteamBridge] addRouterPatch failed:", e);
    return { remove: () => {} };
  }

  return {
    remove: () => {
      if (removed || !token) return;
      removed = true;
      try {
        routerHook.removePatch(path, token);
      } catch (e) {
        console.warn("[SteamBridge] router unpatch failed:", e);
      }
    },
  };
}
