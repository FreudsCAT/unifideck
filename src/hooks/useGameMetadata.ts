/**
 * useGameMetadata — per-appId display metadata with TTL cache.
 *
 * Companion to {@link useGameInfo}. Where `useGameInfo` returns
 * install state (size, install_path, is_installed, deck_rating),
 * this hook returns the rich Steam-Store-derived display payload
 * (developer, publisher, release date, description, genres,
 * deck compatibility category, Metacritic score, …).
 *
 * Cache invariants :
 *  - TTL = 60_000ms — metadata changes far less often than install
 *    state, so we hold it 12× longer than the {@link useGameInfo}
 *    cache to avoid re-RPCing on every panel open.
 *  - Concurrent consumers of the same appId share one in-flight
 *    fetch (de-duplication via the `inflight` promise slot).
 *  - `refresh()` always re-fetches, bypassing the TTL gate.
 */
import { useCallback, useEffect, useState } from "react";
import { useRPC } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import type { GameMetadata } from "../types/api";

interface CacheEntry {
  data: GameMetadata | null;
  ts: number;
  inflight: Promise<GameMetadata | null> | null;
}

const CACHE_TTL = 60_000;
const cache = new Map<number, CacheEntry>();

/** Reactive payload returned by {@link useGameMetadata}. */
export interface UseGameMetadataResult {
  data: GameMetadata | null;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

/**
 * Fetch + cache the panel's rich display metadata for one
 * shortcut. Returns ``data === null`` until the first fetch
 * resolves; consumers should not block panel render on it
 * (loading is a soft hint).
 *
 * @param appId — Steam shortcut app-id, or null for "no game".
 */
export function useGameMetadata(appId: number | null): UseGameMetadataResult {
  const fetch = useRPC<[number], GameMetadata | null>(
    rpcRoutes.getGameMetadataDisplay,
  );
  const [state, setState] = useState<{
    data: GameMetadata | null;
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
      if (cached?.inflight && !force) {
        const data = await cached.inflight;
        setState({ data, loading: false, error: null });
        return;
      }
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

/** Drop the cache entry for one appId so the next render
 *  re-fetches. Drops signed/unsigned variants too, mirroring
 *  {@link invalidateGameInfo}. */
export function invalidateGameMetadata(appId: number): void {
  cache.delete(appId);
  const signed = appId > 0x7fffffff ? appId - 0x100000000 : appId;
  const unsigned = appId < 0 ? appId + 0x100000000 : appId;
  if (signed !== appId) cache.delete(signed);
  if (unsigned !== appId) cache.delete(unsigned);
}

export function _clearGameMetadataCache(): void {
  cache.clear();
}
