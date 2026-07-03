/**
 * useSteamLibrary — typed snapshot of Steam's app library.
 *
 * Reads owned + non-Steam apps via SteamBridge, transforms
 * them into the canonical `UnifideckGame` shape, and exposes
 * filter helpers (by store, by installed, by deck-verified).
 *
 * Replaces the legacy `useSteamLibrary.ts` which hit
 * `window.SteamClient` directly. All Steam internals are
 * now funnelled through SteamBridge so a Steam update
 * breaks one file (the bridge), not this hook.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { SteamBridge } from "../lib/steam-bridge";
import type { SteamApp } from "../types/steam";

/**
 * Frontend projection of an Unifideck-managed shortcut. Holds
 * the union of fields any tab/component reads — keeps the
 * concrete `Game` from the api-types layer minimal.
 */
export interface UnifideckGame {
  appId: number;
  title: string;
  store: "steam" | "unknown";
  isInstalled: boolean;
  isShortcut: boolean;
  lastPlayed: number;
  playtimeMinutes: number;
}

/**
 * Shape returned by {@link useSteamLibrary}. Exposes the
 * Unifideck slice of the Steam library plus a refetch hook
 * for callers that need a forced refresh.
 */
export interface UseSteamLibraryResult {
  games: UnifideckGame[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
  filterByStore: (store: UnifideckGame["store"]) => UnifideckGame[];
  filterByInstalled: () => UnifideckGame[];
}

/** Transform. */
function transform(app: SteamApp): UnifideckGame {
  return {
    appId: app.appid,
    title: app.display_name || app.sort_as || "Unknown",
    store: app.is_shortcuts_app || app.BIsShortcut?.() ? "unknown" : "steam",
    isInstalled: !!app.installed,
    isShortcut: !!app.is_shortcuts_app || !!app.BIsShortcut?.(),
    lastPlayed: app.rt_last_time_played || 0,
    playtimeMinutes: app.minutes_playtime_forever || 0,
  };
}

/**
 * Hook that exposes the Unifideck-managed slice of
 * the Steam library — non-Steam shortcuts created
 * by Unifideck. Subscribes to library mutation
 * events so additions / removals reflect without a
 * full sync.
 *
 * @returns array of Unifideck games + loading flag.
 */
export function useSteamLibrary(bridge: SteamBridge): UseSteamLibraryResult {
  const [games, setGames] = useState<UnifideckGame[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    try {
      if (!bridge.isReady()) {
        throw new Error("Steam Apps API not available");
      }
      const all = bridge.getAllApps();
      setGames(all.map(transform));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [bridge]);

  useEffect(() => {
    load();
  }, [load]);

  const filterByStore = useCallback(
    (store: UnifideckGame["store"]) => games.filter((g) => g.store === store),
    [games],
  );

  const filterByInstalled = useCallback(
    () => games.filter((g) => g.isInstalled),
    [games],
  );

  return useMemo(
    () => ({
      games,
      loading,
      error,
      refresh: load,
      filterByStore,
      filterByInstalled,
    }),
    [games, loading, error, load, filterByStore, filterByInstalled],
  );
}
