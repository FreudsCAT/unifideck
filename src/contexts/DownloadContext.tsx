/**
 * DownloadContext — React wrapper around the boot-time DownloadStore.
 *
 * The heavy lifting (EventBus subscriptions, queue fetching, Ubisoft
 * UPC launch, game-state invalidation) now lives in
 * `stores/download-store.ts` and runs independently of QAM mount.
 *
 * This context provides:
 *   - Reactive `queue` snapshot via `useSyncExternalStore`
 *   - User-initiated mutation actions (install, uninstall, cancel)
 *   - A `refresh` callback for manual re-fetch
 */
import {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useSyncExternalStore,
} from "react";
import { useRPCMutation } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { downloadStore } from "../stores/download-store";
import type { DownloadQueueInfo } from "../types/downloads";
import type { Result, StoreId } from "../types/api";

/** Download context value. */
interface DownloadContextValue {
  queue: DownloadQueueInfo | null;
  loading: boolean;
  installGame: (
    store: StoreId,
    gameId: string,
    options?: { storage?: string; language?: string; title?: string },
  ) => Promise<Result | null>;
  uninstallGame: (
    appId: number,
    deletePrefix?: boolean,
  ) => Promise<Result | null>;
  cancelDownload: (downloadId: string) => Promise<Result | null>;
  /** Queue an update for an already-installed game. */
  updateGame: (appId: number) => Promise<Result | null>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<DownloadContextValue | null>(null);

/**
 * Provider that exposes the download queue and mutation actions.
 * Queue state comes from the boot-time `downloadStore` singleton
 * via `useSyncExternalStore` — no mount-time RPC or EventBus
 * subscriptions happen here.
 */
export const DownloadProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const { queue, loading } = useSyncExternalStore(
    downloadStore.subscribe,
    downloadStore.getSnapshot,
  );

  // RPC mutations — user-initiated, stay in the React layer
  const installMut = useRPCMutation<
    [
      StoreId,
      string,
      { storage?: string; language?: string; title?: string } | undefined,
    ],
    Result
  >(rpcRoutes.installGame);

  const uninstallMut = useRPCMutation<[number, boolean], Result>(
    rpcRoutes.uninstallGame,
  );

  const cancelMut = useRPCMutation<[string, string], Result>(
    rpcRoutes.cancelDownload,
  );

  const updateMut = useRPCMutation<[number], Result>(rpcRoutes.updateGame);

  const refresh = useCallback(async () => {
    await downloadStore.refetch();
  }, []);

  const installGame = useCallback(
    (
      store: StoreId,
      gameId: string,
      options?: { storage?: string; language?: string; title?: string },
    ) => installMut.mutate(store, gameId, options),
    [installMut],
  );

  const uninstallGame = useCallback(
    (appId: number, deletePrefix = false) =>
      uninstallMut.mutate(appId, deletePrefix),
    [uninstallMut],
  );

  const cancelDownload = useCallback(
    (downloadId: string) => {
      const idx = downloadId.indexOf(":");
      const store = idx > 0 ? downloadId.slice(0, idx) : "";
      const gameId = idx > 0 ? downloadId.slice(idx + 1) : downloadId;
      return cancelMut.mutate(store, gameId);
    },
    [cancelMut],
  );

  const updateGame = useCallback(
    (appId: number) => updateMut.mutate(appId),
    [updateMut],
  );

  const value: DownloadContextValue = {
    queue,
    loading,
    installGame,
    uninstallGame,
    cancelDownload,
    updateGame,
    refresh,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

/**
 * Access the DownloadContext value. Throws if used
 * outside `<DownloadProvider>`.
 *
 * @throws Error when the provider is missing.
 */
export function useDownloads(): DownloadContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDownloads called outside <DownloadProvider>");
  return v;
}
