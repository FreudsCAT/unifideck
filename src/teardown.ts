/**
 * Teardown — symmetric cleanup for `definePlugin` unload.
 *
 * Decky's plugin contract requires returning an `unmount`
 * callback from `definePlugin`. This module collects every
 * resource registered at boot time and unregisters them in
 * reverse order. Symmetry with `bootstrap-tasks.ts` :
 * whatever was created there gets removed here.
 *
 * Failures are logged but never thrown — Decky's unmount
 * path is best-effort, an uncaught exception leaves the
 * plugin in a half-loaded state until the next reboot.
 */
import type { RouterPatchHandle } from "./lib/steam-bridge";
import type { CollectionManagerHandle } from "./lib/steam-bridge/collection-manager";
import type { Unregisterable } from "./types/steam";
/**
 * Handles captured during bootstrap that {@link runTeardown}
 * must dispose on plugin unload. Currently the lifetime
 * listener registration ; will grow as Phase 4 integrates
 * watchdog hooks.
 */
export interface TeardownHandles {
  routerPatch?: RouterPatchHandle | null;
  libraryPatch?: RouterPatchHandle | null;
  collectionManager?: CollectionManagerHandle | null;
  appStorePatch?: { remove: () => void } | null;
  overviewEnrichment?: (() => void) | null;
  lifetimeListener?: Unregisterable | null;
  launcherToastPoll?: (() => void) | null;
}
/**
 * Run every disposer captured during bootstrap, in
 * reverse registration order. Each disposer is
 * isolated so one throwing does not skip the others.
 *
 * Decky Loader calls this on plugin unload — leaks
 * here can survive across reloads of the dev cycle
 * and produce subtle phantom listeners.
 */
export function runTeardown(handles: TeardownHandles): void {
  if (handles.launcherToastPoll) {
    try {
      handles.launcherToastPoll();
    } catch (e) {
      console.warn("[Teardown] launcher toast poll stop failed:", e);
    }
  }
  if (handles.lifetimeListener) {
    try {
      handles.lifetimeListener.unregister();
    } catch (e) {
      console.warn("[Teardown] lifetime listener unregister failed:", e);
    }
  }
  if (handles.overviewEnrichment) {
    try {
      handles.overviewEnrichment();
    } catch (e) {
      console.warn("[Teardown] overview enrichment stop failed:", e);
    }
  }
  if (handles.appStorePatch) {
    try {
      handles.appStorePatch.remove();
    } catch (e) {
      console.warn("[Teardown] app-store patch remove failed:", e);
    }
  }
  if (handles.collectionManager) {
    try {
      handles.collectionManager.remove();
    } catch (e) {
      console.warn("[Teardown] collection manager remove failed:", e);
    }
  }
  if (handles.libraryPatch) {
    try {
      handles.libraryPatch.remove();
    } catch (e) {
      console.warn("[Teardown] library patch remove failed:", e);
    }
  }
  if (handles.routerPatch) {
    try {
      handles.routerPatch.remove();
    } catch (e) {
      console.warn("[Teardown] router patch remove failed:", e);
    }
  }
}
