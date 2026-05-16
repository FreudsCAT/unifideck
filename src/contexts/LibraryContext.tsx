/**
 * LibraryContext — boot the Unifideck game cache + ProtonDB cache.
 *
 * Reads `get_all_unifideck_games` and `get_protondb_cache` once on
 * mount and feeds the module-level caches in
 * `lib/library-filters` and `lib/protondb-cache`. Exposes a
 * `refresh()` so the sync-finished event handler can repopulate.
 *
 * Replaces staging's module globals (`unifideckGameCache`,
 * `gameStateVersion`) being populated from `src/index.tsx` —
 * the caches still live as module-level state (the library-patch
 * hook must call them from non-component contexts) but their
 * lifecycle is now owned by this provider.
 */
import { createContext, FC, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { useRPC } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import {
  loadCompatCacheFromBackend,
  isCompatCacheLoaded,
} from "../lib/protondb-cache";
import {
  updateUnifideckCache,
  unifideckGameCache,
  type UnifideckGameInput,
} from "../lib/library-filters";
import type { Game, StoreId } from "../types/api";

interface LibraryContextValue {
  ready: boolean;
  refresh: () => Promise<void>;
}

const Ctx = createContext<LibraryContextValue | null>(null);

function isNonSteamStore(s: StoreId): s is Exclude<StoreId, "steam"> {
  return s !== "steam";
}

export const LibraryProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const fetchGames = useRPC<[], Game[]>(rpcRoutes.getAllUnifideckGames);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const games = await fetchGames();
      const inputs: UnifideckGameInput[] = [];
      for (const g of games ?? []) {
        if (g.app_id == null) continue;
        if (!isNonSteamStore(g.store)) continue;
        inputs.push({
          appId: g.app_id,
          store: g.store,
          isInstalled: g.is_installed,
          steamAppId: g.steam_app_id,
        });
      }
      updateUnifideckCache(inputs);
    } catch (e) {
      console.error("[LibraryContext] failed to load unifideck games", e);
    }
    if (!isCompatCacheLoaded()) {
      await loadCompatCacheFromBackend();
    }
    setReady(true);
  }, [fetchGames]);

  useEffect(() => {
    void refresh();
    const onSync = () => void refresh();
    window.addEventListener("unifideck-sync-completed", onSync);
    return () => window.removeEventListener("unifideck-sync-completed", onSync);
  }, [refresh]);

  return (
    <Ctx.Provider value={{ ready, refresh }}>{children}</Ctx.Provider>
  );
};

export function useLibrary(): LibraryContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLibrary called outside <LibraryProvider>");
  return v;
}

export function getUnifideckGameCount(): number {
  return unifideckGameCache.size;
}
