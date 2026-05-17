/**
 * LibraryContext — thin React subscription wrapper.
 *
 * The unifideck game cache is now populated eagerly at plugin
 * init by ``startUnifideckCacheAutoload`` in
 * ``lib/library-filters`` — independent of QAM mount, so the
 * library tab patches have data on first render. This provider
 * exists only to expose a reactive ``ready`` flag + ``refresh``
 * trigger for QAM components that want to re-fetch on demand.
 *
 * Also loads the ProtonDB compat cache on mount (still
 * QAM-gated since it's only read by QAM-rendered surfaces).
 */
import {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  loadCompatCacheFromBackend,
  isCompatCacheLoaded,
} from "../lib/protondb-cache";
import { loadUnifideckCache, unifideckGameCache } from "../lib/library-filters";

interface LibraryContextValue {
  ready: boolean;
  refresh: () => Promise<void>;
}

const Ctx = createContext<LibraryContextValue | null>(null);

export const LibraryProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [ready, setReady] = useState(() => unifideckGameCache.size > 0);

  const refresh = useCallback(async () => {
    await loadUnifideckCache();
    if (!isCompatCacheLoaded()) {
      await loadCompatCacheFromBackend();
    }
    setReady(true);
  }, []);

  useEffect(() => {
    void refresh();
    const onSync = () => void refresh();
    window.addEventListener("unifideck-sync-completed", onSync);
    return () => window.removeEventListener("unifideck-sync-completed", onSync);
  }, [refresh]);

  return <Ctx.Provider value={{ ready, refresh }}>{children}</Ctx.Provider>;
};

export function useLibrary(): LibraryContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLibrary called outside <LibraryProvider>");
  return v;
}

export function getUnifideckGameCount(): number {
  return unifideckGameCache.size;
}
