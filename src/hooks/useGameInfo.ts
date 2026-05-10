/**
 * useGameInfo — per-appId info fetch with TTL cache.
 *
 * Replaces the global `gameInfoCache` Map that lived in the
 * old `index.tsx` (and was passed via setter callbacks all
 * over the place). Each component that needs game info just
 * calls `useGameInfo(appId)` and gets reactive {data, loading,
 * error, refresh}. The hook shares a module-level cache so
 * concurrent consumers of the same appId don't re-fetch.
 *
 * Cache invariants :
 *  - TTL = 5000ms (matches old behaviour)
 *  - Same appId across components = single fetch in flight
 *  - `refresh()` always re-fetches (bypasses cache)
 */
import { useCallback, useEffect, useState } from "react";
import { useRPC } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import type { Game } from "../types/api";

/** Cache entry. */
interface CacheEntry {
  data: Game | null;
  ts: number;
  inflight: Promise<Game | null> | null;
}

const CACHE_TTL = 5000;
const cache = new Map<number, CacheEntry>();

/**
 * Aggregated game info returned by {@link useGameInfo} —
 * description, scores, artwork URLs, playtime fragments —
 * with loading and error flags propagated through.
 */
export interface UseGameInfoResult {
  data: Game | null;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

/**
 * Hook that aggregates all metadata Unifideck has on
 * a given game : description, scores, artwork,
 * playtime stats. Uses `useRPCQuery` under the hood
 * with a sensible cache TTL so opening the info panel
 * is instant on revisits.
 *
 * @param appId — Steam shortcut app-id.
 * @returns aggregated info + loading/error flags.
 */
export function useGameInfo(appId: number | null): UseGameInfoResult {
  const fetch = useRPC<[number], Game>(rpcRoutes.getGameMetadata);
  const [state, setState] = useState<{
    data: Game | null;
    loading: boolean;
    error: Error | null;
  }>({ data: null, loading: appId != null, error: null });

  const load = useCallback(
    async (force: boolean): Promise<void> => {
      if (appId == null) {
        setState({ data: null, loading: false, error: null });
        return;
      }

      const cached = cache.get(appId);
      if (!force && cached && Date.now() - cached.ts < CACHE_TTL) {
        setState({ data: cached.data, loading: false, error: null });
        return;
      }

      // De-duplicate concurrent in-flight fetches
      if (cached?.inflight && !force) {
        const data = await cached.inflight;
        setState({ data, loading: false, error: null });
        return;
      }

      setState((s) => ({ ...s, loading: true, error: null }));
      const promise = fetch(appId).then(
        (data) => {
          cache.set(appId, { data, ts: Date.now(), inflight: null });
          return data;
        },
        (err) => {
          cache.set(appId, { data: null, ts: Date.now(), inflight: null });
          throw err;
        },
      );

      cache.set(appId, {
        data: cached?.data ?? null,
        ts: cached?.ts ?? 0,
        inflight: promise,
      });

      try {
        const data = await promise;
        setState({ data, loading: false, error: null });
      } catch (err) {
        setState({ data: null, loading: false, error: err as Error });
      }
    },
    [appId, fetch],
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  const refresh = useCallback(() => load(true), [load]);

  return { ...state, refresh };
}

/** Test/dev helper — clear the module-level cache. Not
 *  exposed via the barrel; imported only by vitest specs. */
export function _clearGameInfoCache(): void {
  cache.clear();
}
