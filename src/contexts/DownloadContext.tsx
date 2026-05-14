/**
 * DownloadContext — install queue + progress reactivity.
 *
 * Provides a real-time view of the download queue. The
 * snapshot updates from two sources :
 *  1. Initial fetch via `get_download_queue`.
 *  2. Live updates via DOWNLOAD_* events from the EventBus.
 *
 * Design note: we deliberately avoid storing per-game
 * progress as separate atoms. The queue is an inherently
 * ordered structure and consumers benefit from receiving
 * the full snapshot — re-rendering a 5-item list per tick
 * is cheaper than orchestrating 5 selectors.
 */
import React, {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { useRPCMutation, useRPCQuery } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { useEventBus, EventBusClient } from "../api/event-bus-client";
import { Events } from "../types/events";
import type { DownloadItem, DownloadQueueInfo } from "../types/downloads";
import type { Result, StoreId } from "../types/api";

/** Wire shape the backend currently returns from
 *  `get_download_queue` — `{queued, running}`. The frontend
 *  needs `{current, queued, finished, state}`, so we adapt
 *  defensively : `current` = first running item, `finished`
 *  = empty until backend exposes a history, `state` derived
 *  from `running.length`. */
function adaptQueue(raw: unknown): DownloadQueueInfo {
  const obj = (typeof raw === "object" && raw !== null)
    ? raw as Record<string, unknown>
    : {};
  const queued = Array.isArray(obj.queued) ? obj.queued as DownloadItem[] : [];
  const running = Array.isArray(obj.running) ? obj.running as DownloadItem[] : [];
  const finished = Array.isArray(obj.finished) ? obj.finished as DownloadItem[] : [];
  const current = (obj.current as DownloadItem | undefined) ?? running[0] ?? null;
  return {
    success: true,
    queued,
    finished,
    current,
    state: running.length > 0 ? "running" : "idle",
  };
}

/** Download context value. */
interface DownloadContextValue {
  queue: DownloadQueueInfo | null;
  loading: boolean;
  installGame: (
    store: StoreId,
    gameId: string,
    options?: { storage?: string; language?: string },
  ) => Promise<Result | null>;
  uninstallGame: (appId: number) => Promise<Result | null>;
  cancelDownload: (downloadId: string) => Promise<Result | null>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<DownloadContextValue | null>(null);

/**
 * Provider that mirrors the backend download queue
 * to React state. Subscribes to `DOWNLOAD_QUEUED`,
 * `DOWNLOAD_PROGRESS`, `DOWNLOAD_COMPLETE` and
 * `DOWNLOAD_FAILED` events for incremental updates ;
 * a periodic `get_download_queue` poll guards against
 * dropped events.
 */
export const DownloadProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [queue, setQueue] = useState<DownloadQueueInfo | null>(null);
  const initial = useRPCQuery<[], unknown>(rpcRoutes.getDownloadQueue, []);

  useEffect(() => {
    if (initial.data) setQueue(adaptQueue(initial.data));
  }, [initial.data]);
  // RPC mutations
  const installMut = useRPCMutation<
    [StoreId, string, { storage?: string; language?: string } | undefined],
    Result
  >(rpcRoutes.installGame);

  const uninstallMut = useRPCMutation<[number], Result>(
    rpcRoutes.uninstallGame,
  );

  const cancelMut = useRPCMutation<[string], Result>(
    rpcRoutes.cancelDownload,
  );

  /** Refresh. */
  const refresh = useCallback(async () => {
    await initial.refetch();
  }, [initial]);

  /** Refetch queue. */
  const refetchQueue = useCallback(() => {
    void refresh();
  }, [refresh]);

  useEventBus(Events.DOWNLOAD_QUEUED, refetchQueue);

  useEventBus(Events.DOWNLOAD_STARTED, () => {
    EventBusClient.bumpToFast();
    refetchQueue();
  });

  useEventBus(Events.DOWNLOAD_PROGRESS, (payload) => {
    // Update only the current item in place; full refetch is
    // wasteful for high-frequency progress events.
    setQueue((prev) => prev && {
      ...prev,
      current: prev.current && {
        ...prev.current,
        progress_percent: (payload.progress as number) ?? prev.current.progress_percent,
        speed_mbps: (payload.speed_mbps as number) ?? prev.current.speed_mbps,
        eta_seconds: (payload.eta_seconds as number) ?? prev.current.eta_seconds,
      },
    });
  });

  useEventBus(Events.DOWNLOAD_COMPLETE, refetchQueue);
  useEventBus(Events.DOWNLOAD_FAILED, refetchQueue);
  useEventBus(Events.DOWNLOAD_CANCELLED, refetchQueue);

  const installGame = useCallback(
    (
      store: StoreId,
      gameId: string,
      options?: { storage?: string; language?: string },
    ) => installMut.mutate(store, gameId, options),
    [installMut],
  );

  const uninstallGame = useCallback(
    (appId: number) => uninstallMut.mutate(appId),
    [uninstallMut],
  );

  const cancelDownload = useCallback(
    (downloadId: string) => cancelMut.mutate(downloadId),
    [cancelMut],
  );

  const value: DownloadContextValue = {
    queue,
    loading: initial.loading,
    installGame, uninstallGame, cancelDownload, refresh,
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
