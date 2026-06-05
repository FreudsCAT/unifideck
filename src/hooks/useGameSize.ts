/**
 * useGameSize — lazy, non-blocking "Space Required" size fetch.
 *
 * The size lookup lives in its own RPC (`get_game_size_bytes`)
 * rather than in `get_game_info`, because resolving a download
 * size shells out to `legendary info` / `gogdl` (subprocess /
 * network) and can take seconds. `usePlaySection` + the game-info
 * panel both gate on `get_game_info`, so doing the size work there
 * stalled the whole custom UI behind Steam's native section. This
 * hook fetches the size separately, in an effect, exactly like
 * `MetaInline` already does for Last Played — the row renders
 * immediately and the size fills in a moment later.
 *
 * A module-level cache (keyed by appId) de-dupes the fetch so the
 * play-section `MetaInline` and the info-panel size cell share one
 * round-trip per game.
 */
import { useEffect, useState } from "react";
import { call } from "@decky/api";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";

const cache = new Map<number, number>();
const inflight = new Map<number, Promise<number>>();

async function fetchSize(appId: number): Promise<number> {
  const cached = cache.get(appId);
  if (cached != null) return cached;
  const existing = inflight.get(appId);
  if (existing) return existing;

  const promise = (async () => {
    const raw = await call<[number], unknown>(rpcRoutes.getGameSizeBytes, appId);
    const bytes = unwrapRpcEnvelope<number>(raw, {
      route: rpcRoutes.getGameSizeBytes,
      throwing: false,
    });
    const value = typeof bytes === "number" && bytes > 0 ? bytes : 0;
    cache.set(appId, value);
    return value;
  })().finally(() => { inflight.delete(appId); });

  inflight.set(appId, promise);
  return promise;
}

/**
 * Resolve the install / download size (bytes) for a Steam shortcut
 * appId. Returns `undefined` until the fetch resolves; `0` means the
 * size is unknown (e.g. Ubisoft / Microsoft, or an offline store).
 *
 * @param appId — Steam shortcut app-id, or null to skip.
 */
export function useGameSize(appId: number | null): number | undefined {
  const [size, setSize] = useState<number | undefined>(
    appId != null ? cache.get(appId) : undefined,
  );

  useEffect(() => {
    if (appId == null) {
      setSize(undefined);
      return;
    }
    const cached = cache.get(appId);
    if (cached != null) {
      setSize(cached);
      return;
    }
    let cancelled = false;
    void fetchSize(appId).then((bytes) => {
      if (!cancelled) setSize(bytes);
    }).catch(() => { /* size is best-effort — leave undefined */ });
    return () => { cancelled = true; };
  }, [appId]);

  return size;
}
