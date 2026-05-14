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
  // Backend's `get_game_metadata(store, game_id)` requires a
  // store/game-id pair we don't have at the appId boundary.
  // `get_game_info(app_id)` is the right route for "look up
  // by Steam shortcut appid".
  const fetch = useRPC<[number], Game>(rpcRoutes.getGameInfo);
  // Lazy priming : if the module-level cache has ANY entry for
  // this appId (fresh OR stale), seed the initial state with it
  // so consumers paint immediately. Stale data still triggers a
  // background refresh below.
  const [state, setState] = useState<{
    data: Game | null;
    loading: boolean;
    error: Error | null;
  }>(() => {
    if (appId == null) return { data: null, loading: false, error: null };
    const cached = cache.get(appId);
    return {
      data: cached?.data ?? null,
      loading: cached?.data == null,
      error: null,
    };
  });

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

      // Stale-while-revalidate : if we have ANY cached data,
      // keep showing it while the background refresh runs.
      setState((s) => ({
        data: s.data ?? cached?.data ?? null,
        loading: s.data == null && cached?.data == null,
        error: null,
      }));
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

/** Drop the cache entry for one appId so the next render
 *  re-fetches. Called after destructive actions (uninstall,
 *  cancel) where `is_installed` flips. Mirrors the legacy
 *  `gameInfoCache.delete(appId)` semantics — also drops the
 *  signed/unsigned variants since Steam shortcuts may be
 *  represented either way in the cache. */
export function invalidateGameInfo(appId: number): void {
  cache.delete(appId);
  const signed = appId > 0x7FFFFFFF ? appId - 0x100000000 : appId;
  const unsigned = appId < 0 ? appId + 0x100000000 : appId;
  if (signed !== appId) cache.delete(signed);
  if (unsigned !== appId) cache.delete(unsigned);
}
