/**
 * useGamePlaytime — per-game playtime for the Play section.
 *
 * Fetches our own tracking DB's aggregate for one game via the
 * `get_playtime` RPC (backed by `services/playtime/`), returning the
 * local total, the store's authoritative cross-device total (GOG/Epic,
 * `null` until first synced), and the last-played timestamp.
 *
 * This is the read counterpart to the playtime write path
 * (`notify_game_launched`/`notify_game_stopped` → `GAME_LAUNCHED` →
 * `PlaytimeService`). The Play section previously sourced "Last Played"
 * from Steam's `GetPlaytime` only, which is wrong/empty for non-Steam
 * launcher games — this hook lets the UI prefer our own data.
 *
 * Unlike `useGameSize`, playtime is volatile (it changes after every
 * session ends), so there is no persistent module cache: the hook
 * refetches on each mount. The component is re-mounted whenever the
 * app-details page is opened, so this stays at one round-trip per view.
 */
import { useEffect, useState } from "react";
import { call } from "@decky/api";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import type { PlaytimeEntry } from "../types/playtime";

/**
 * Resolve playtime stats for one game from our tracking DB.
 *
 * @param store — store id (e.g. `"gog"`, `"epic"`), or null/undefined to skip.
 * @param gameId — store-native game id (`Game.id` / `store_game_id`), or
 *   null/undefined to skip.
 * @returns the {@link PlaytimeEntry}, or `undefined` until it resolves /
 *   when skipped. A game we've never recorded resolves to an all-zero entry.
 */
export function useGamePlaytime(
  store: string | null | undefined,
  gameId: string | null | undefined,
): PlaytimeEntry | undefined {
  const [entry, setEntry] = useState<PlaytimeEntry | undefined>(undefined);

  useEffect(() => {
    if (!store || !gameId) {
      setEntry(undefined);
      return;
    }
    let cancelled = false;
    void call<[string, string], unknown>(rpcRoutes.getPlaytime, store, gameId)
      .then((raw) => {
        if (cancelled) return;
        const data = unwrapRpcEnvelope<PlaytimeEntry>(raw, {
          route: rpcRoutes.getPlaytime,
          throwing: false,
        });
        setEntry(data ?? undefined);
      })
      .catch(() => {
        /* playtime is best-effort — leave undefined (Steam fallback) */
      });
    return () => {
      cancelled = true;
    };
  }, [store, gameId]);

  return entry;
}
