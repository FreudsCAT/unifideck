/**
 * Achievement hooks.
 *
 * `useAchievements` — loads a game's achievements (definitions + this user's
 *   unlock status) on mount and exposes a `refresh` that bypasses the backend
 *   TTL cache. Backed by `get_game_achievements` (may hit the network), so it's
 *   used on demand (modal open), never on the render hot path.
 *
 * `useLastSessionAchievements` — a fast, network-free read of the watcher's
 *   persisted last-session summary (`get_last_session_achievements`), for the
 *   game-info panel's "last session" row.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { rpcRoutes } from "../api/rpc-routes";
import { RpcError } from "../api/rpc-errors";
import { useRPC, useRPCQuery } from "../api/useRPC";
import type { GameAchievements, LastSessionAchievements } from "../types/api";

export interface UseAchievementsResult {
  data: GameAchievements | null;
  loading: boolean;
  error: RpcError | null;
  /** Re-fetch bypassing the backend cache (manual "Refresh"). */
  refresh: () => Promise<void>;
}

/** Load + cache a game's achievements; `enabled=false` defers the fetch. */
export function useAchievements(
  store: string,
  gameId: string,
  enabled = true,
): UseAchievementsResult {
  const rpc = useRPC<[string, string, boolean], GameAchievements>(
    rpcRoutes.getGameAchievements,
  );
  const [data, setData] = useState<GameAchievements | null>(null);
  const [error, setError] = useState<RpcError | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const cancelledRef = useRef(false);

  const load = useCallback(
    async (force: boolean): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const result = await rpc(store, gameId, force);
        if (!cancelledRef.current) setData(result);
      } catch (e) {
        if (!cancelledRef.current) setError(e as RpcError);
      } finally {
        if (!cancelledRef.current) setLoading(false);
      }
    },
    [rpc, store, gameId],
  );

  useEffect(() => {
    cancelledRef.current = false;
    if (enabled) void load(false);
    return () => {
      cancelledRef.current = true;
    };
  }, [enabled, load]);

  const refresh = useCallback(() => load(true), [load]);
  return { data, loading, error, refresh };
}

/** Last play session's unlock summary, or null. Fast, network-free read. */
export function useLastSessionAchievements(
  store: string,
  gameId: string,
  enabled = true,
): LastSessionAchievements | null {
  const { data } = useRPCQuery<
    [string, string],
    LastSessionAchievements | null
  >(rpcRoutes.getLastSessionAchievements, [store, gameId], { enabled });
  return data ?? null;
}
