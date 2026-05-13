/**
 * Router patch lifecycle.
 *
 * Wraps `@decky/api`'s `routerHook.addPatch` with a typed
 * handle. Plugins must keep a reference to the handle and
 * call `.remove()` in their unload path; otherwise patches
 * leak across plugin reloads and accumulate.
 */
import { routerHook } from "@decky/api";

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
  let unpatch: (() => void) | undefined;

  try {
    unpatch = routerHook.addPatch(path, patch as never);
  } catch (e) {
    console.error("[SteamBridge] addRouterPatch failed:", e);
    return { remove: () => {} };
  }

  return {
    remove: () => {
      if (removed) return;
      removed = true;
      try {
        unpatch?.();
      } catch (e) {
        console.warn("[SteamBridge] router unpatch failed:", e);
      }
    },
  };
}
