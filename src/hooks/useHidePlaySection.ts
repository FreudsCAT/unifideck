/**
 * useHidePlaySection — CDP-driven hide/show of Steam's
 * native action-bar PlaySection.
 *
 * The backend (cdp_inject.py) hide is stateless and works
 * per-call; React, however, re-creates the action bar a few
 * frames after mount (and again on later SPA navigation back
 * to the page). This hook re-invokes the backend hide on the
 * same cadence staging used, which has the field-tested
 * coverage:
 *
 *   0 ms  → immediate (catches the first paint)
 *   50 ms → quick retry for the React commit pass
 *   150 ms → DOM-settle after layout
 *   300 ms → React reconciliation tail
 *   600 ms → late re-renders (e.g. data fetches resolving)
 *   2000 ms (recurring) → catches SPA route returns
 *
 * Unhide on unmount is also serialised through the same
 * per-appId promise chain so a fast remount can't end up
 * with a stale unhide stomping on a new hide.
 */
import { useEffect } from "react";
import { useRPC } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";

const chain = new Map<number, Promise<void>>();
const generation = new Map<number, number>();

interface HideResult {
  ok: boolean;
  outcome?: string;
  error?: string;
}

function appendOp(appId: number, op: () => Promise<void>): void {
  const tail = chain.get(appId) ?? Promise.resolve();
  const next = tail.then(op).catch((e) => {
    console.warn(`[useHidePlaySection] op failed for ${appId}:`, e);
  });
  chain.set(appId, next);
}

/** Hide Steam's native play section for `appId` while the
 *  consumer component is mounted. Restores on unmount. */
export function useHidePlaySection(
  appId: number | null,
  enabled: boolean,
): void {
  const hide = useRPC<[number], HideResult>(rpcRoutes.hidePlaySection);
  const unhide = useRPC<[number], HideResult>(rpcRoutes.unhidePlaySection);

  useEffect(() => {
    if (appId == null || !enabled) return;
    // Generation discriminator: stale ops dropped if the same
    // appId mounts/unmounts/remounts faster than RPC roundtrip.
    const gen = (generation.get(appId) ?? 0) + 1;
    generation.set(appId, gen);

    const doHide = (): void => {
      appendOp(appId, async () => {
        if (generation.get(appId) !== gen) return;
        const result = await hide(appId);
        // Surface the backend outcome in the CEF console so we can
        // tell from DevTools alone whether the burst is reaching JS
        // ("hidden"), missing the button ("not_found"), or silently
        // no-op'ing because CDP isn't connected ("cdp_not_connected").
        console.debug(`[useHidePlaySection] hide(${appId}) =>`, result);
      });
    };

    // Burst: immediate + short follow-ups to win the race with
    // React's mount/commit/reconcile cycle.
    doHide();
    const t1 = window.setTimeout(doHide, 50);
    const t2 = window.setTimeout(doHide, 150);
    const t3 = window.setTimeout(doHide, 300);
    const t4 = window.setTimeout(doHide, 600);
    // Persistent poll for SPA re-renders (Settings → B → back).
    const interval = window.setInterval(doHide, 2000);

    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.clearTimeout(t3);
      window.clearTimeout(t4);
      window.clearInterval(interval);
      const gen2 = (generation.get(appId) ?? 0) + 1;
      generation.set(appId, gen2);
      appendOp(appId, async () => {
        if (generation.get(appId) !== gen2) return;
        await unhide(appId);
      });
    };
  }, [appId, enabled, hide, unhide]);
}
