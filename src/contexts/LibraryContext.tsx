/**
 * LibraryContext — thin React subscription wrapper.
 *
 * The unifideck game cache is populated eagerly at plugin init by
 * ``startUnifideckCacheAutoload`` in ``lib/library-filters`` —
 * independent of QAM mount.
 *
 * The ProtonDB compat cache is also loaded at boot (in the
 * `definePlugin` callback) so library tab patches have compat
 * data on first render without waiting for a QAM open.
 *
 * This provider exists to expose a reactive ``ready`` flag +
 * ``refresh`` trigger for QAM components that want to re-fetch
 * on demand.
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
import { loadCompatCacheFromBackend } from "../lib/protondb-cache";
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
    await loadCompatCacheFromBackend();
    setReady(true);
  }, []);

  useEffect(() => {
    // On mount, just check if the cache is already populated (from boot).
    // If not, load it now (first QAM open before boot finished).
    if (!ready) {
      void refresh();
    }
    const onSync = () => void refresh();
    window.addEventListener("unifideck-sync-completed", onSync);
    return () => window.removeEventListener("unifideck-sync-completed", onSync);
  }, [refresh, ready]);

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
