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

interface DeckyLoaderShape {
  routerHook?: {
    routerState?: {
      _routePatches?: Map<string, RoutePatch[]>;
    };
  };
}

/** Best-effort cleanup of stale duplicate patches for ``path``
 *  whose ``.toString()`` matches our new ``patch``. Decky's
 *  spam-loading during plugin install can leave dangling
 *  references behind; without this, the old (stale) patch wins
 *  and our latest code never runs. Pattern mirrors TabMaster's
 *  ``addPatch`` helper. */
function purgeDuplicatePatches(path: string, patch: RoutePatch): void {
  try {
    const loader = (
      window as unknown as { DeckyPluginLoader?: DeckyLoaderShape }
    ).DeckyPluginLoader;
    const patches = loader?.routerHook?.routerState?._routePatches?.get(path);
    if (!patches) return;
    const newSrc = patch.toString();
    for (const existing of [...patches]) {
      if (existing.toString() === newSrc) {
        try {
          routerHook.removePatch(path, existing);
        } catch (e) {
          console.warn("[SteamBridge] purge duplicate patch failed:", e);
        }
      }
    }
  } catch (e) {
    console.warn("[SteamBridge] purgeDuplicatePatches inspect failed:", e);
  }
}

/** Patch a router route. The `patch` function receives the
 *  rendered React node for the route and must return a
 *  (possibly modified) node. */
export function addRouterPatch(
  path: string,
  patch: (node: unknown) => unknown,
): RouterPatchHandle {
  let removed = false;
  let token: RoutePatch | undefined;
  const typedPatch = patch as unknown as RoutePatch;

  purgeDuplicatePatches(path, typedPatch);

  try {
    // addPatch returns a RoutePatch token that must be passed
    // back to removePatch to undo — not an unpatch function.
    token = routerHook.addPatch(path, typedPatch);
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
