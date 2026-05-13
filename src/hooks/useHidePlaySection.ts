/**
 * useHidePlaySection — CDP-driven hide/show of Steam's
 * native play section.
 *
 * When `<PlaySectionWrapper>` decides to render a Unifideck
 * override, Steam's own play button must be hidden (otherwise
 * users see two buttons). This hook serializes inject /
 * remove operations per appId so two rapid mode flips don't
 * leave the DOM in an inconsistent state.
 *
 * Backend exposes two endpoints (UIRPCMixin) :
 *  - `hide_play_section(app_id)`   → CDP injects hiding CSS
 *  - `unhide_play_section(app_id)` → CDP removes the rule
 *
 * The serialization mechanism is a per-appId promise chain
 * stored in a module-level Map. Each new operation is
 * appended to its appId's chain, ensuring strict ordering :
 * inject A → remove A → inject A is processed in that order
 * regardless of timing.
 */
import { useEffect } from "react";
import { useRPC } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";

const chain = new Map<number, Promise<void>>();
const generation = new Map<number, number>();

/** Append op. */
function appendOp(appId: number, op: () => Promise<void>): void {
  const tail = chain.get(appId) ?? Promise.resolve();
  const next = tail.then(op).catch((e) => {
    console.warn(`[useHidePlaySection] op failed for ${appId}:`, e);
  });
  chain.set(appId, next);
}

/** Hide Steam's native play section for `appId` while the
 *  consumer component is mounted. Restores on unmount. */
export function useHidePlaySection(appId: number | null, enabled: boolean): void {
  const hide = useRPC<[number], { ok: boolean }>(
    rpcRoutes.hidePlaySection,
  );

  const unhide = useRPC<[number], { ok: boolean }>(
    rpcRoutes.unhidePlaySection,
  );

  useEffect(() => {
    if (appId == null || !enabled) return;
    /* Bump generation — discriminates stale ops if the same
    * appId mounts/unmounts/remounts faster than RPC roundtrip */
    const gen = (generation.get(appId) ?? 0) + 1;
    generation.set(appId, gen);

    appendOp(appId, async () => {
      if (generation.get(appId) !== gen) return;
      await hide(appId);
    });

    return () => {
      const gen2 = (generation.get(appId) ?? 0) + 1;
      generation.set(appId, gen2);
      appendOp(appId, async () => {
        if (generation.get(appId) !== gen2) return;
        await unhide(appId);
      });
    };
  }, [appId, enabled, hide, unhide]);
}
