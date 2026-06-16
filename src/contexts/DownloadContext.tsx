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
import {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useRPCMutation, useRPCQuery } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { useEventBus, EventBusClient } from "../api/event-bus-client";
import { Events } from "../types/events";
import { invalidateGameInfo } from "../hooks/useGameInfo";
import { bumpGameStateVersion } from "../lib/game-state-version";
import { launchUbisoftInstallViaShortcut } from "../utils/ubisoftShortcutLaunch";
import type { DownloadItem, DownloadQueueInfo } from "../types/downloads";
import type { Result, StoreId } from "../types/api";

/** Pull the appId out of a DOWNLOAD_* event payload. The worker
 *  emits ``game=Game(...)`` on COMPLETE/FAILED/CANCELLED; the
 *  bus serialises the dataclass to a dict so we read
 *  ``payload.game.app_id``. */
function extractAppId(payload: unknown): number | null {
  if (!payload || typeof payload !== "object") return null;
  const game = (payload as { game?: { app_id?: unknown } }).game;
  if (!game || typeof game !== "object") return null;
  const id = (game as { app_id?: unknown }).app_id;
  return typeof id === "number" ? id : null;
}

/** Build the ``"<store>:<game_id>"`` key from a DOWNLOAD_* terminal
 *  payload's ``item`` so we can clear the Ubisoft launch-dedupe set
 *  once an install finishes / fails / is cancelled. */
function extractStoreGameId(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const item = (payload as { item?: { store?: unknown; game_id?: unknown } })
    .item;
  if (!item || typeof item !== "object") return null;
  const store = (item as { store?: unknown }).store;
  const gameId = (item as { game_id?: unknown }).game_id;
  if (typeof store !== "string" || typeof gameId !== "string") return null;
  return `${store}:${gameId}`;
}

/** Wire shape the backend currently returns from
 *  `get_download_queue` — `{queued, running}`. The frontend
 *  needs `{current, queued, finished, state}`, so we adapt
 *  defensively : `current` = first running item, `finished`
 *  = empty until backend exposes a history, `state` derived
 *  from `running.length`. */
function adaptQueue(raw: unknown): DownloadQueueInfo {
  const obj =
    typeof raw === "object" && raw !== null
      ? (raw as Record<string, unknown>)
      : {};
  const queued = Array.isArray(obj.queued)
    ? (obj.queued as DownloadItem[])
    : [];
  const running = Array.isArray(obj.running)
    ? (obj.running as DownloadItem[])
    : [];
  const finished = Array.isArray(obj.finished)
    ? (obj.finished as DownloadItem[])
    : [];
  const current =
    (obj.current as DownloadItem | undefined) ?? running[0] ?? null;
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
    options?: { storage?: string; language?: string; title?: string },
  ) => Promise<Result | null>;
  uninstallGame: (
    appId: number,
    deletePrefix?: boolean,
  ) => Promise<Result | null>;
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

  // Ubisoft installs we've already asked Steam to RunGame for, so a
  // replayed UBISOFT_INSTALL_LAUNCH_REQUESTED event (the bus drains
  // buffered events on reconnect) can't open UPC twice. Cleared per-game
  // on the install's terminal event.
  const ubisoftLaunchedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (initial.data) setQueue(adaptQueue(initial.data));
  }, [initial.data]);
  // RPC mutations
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

  // Backend RPC signature is ``cancel_download(store, game_id)``
  // — two positional args. The frontend's download.id is the
  // combined form ``"store:game_id"`` so we split before
  // dispatch (see ``cancelDownload`` below).
  const cancelMut = useRPCMutation<[string, string], Result>(
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
    setQueue(
      (prev) =>
        prev && {
          ...prev,
          current: prev.current && {
            ...prev.current,
            progress_percent:
              (payload.progress as number) ?? prev.current.progress_percent,
            speed_mbps:
              (payload.speed_mbps as number) ?? prev.current.speed_mbps,
            eta_seconds:
              (payload.eta_seconds as number) ?? prev.current.eta_seconds,
          },
        },
    );
  });

  // On terminal events (complete / failed / cancelled), also
  // invalidate the per-game info cache and bump the state
  // version. Otherwise ``useGameInfo`` keeps serving the
  // pre-install snapshot (``is_installed=false``) for the full
  // 5 s TTL and the Play section stays on "Install" even
  // after a successful download.
  const onDownloadTerminal = useCallback(
    (payload: unknown) => {
      const appId = extractAppId(payload);
      if (appId != null) {
        invalidateGameInfo(appId);
        bumpGameStateVersion(appId);
      }
      // Allow a future re-install of the same game to open UPC again.
      const storeGameId = extractStoreGameId(payload);
      if (storeGameId) ubisoftLaunchedRef.current.delete(storeGameId);
      refetchQueue();
    },
    [refetchQueue],
  );

  useEventBus(Events.DOWNLOAD_COMPLETE, onDownloadTerminal);
  useEventBus(Events.DOWNLOAD_FAILED, onDownloadTerminal);
  useEventBus(Events.DOWNLOAD_CANCELLED, onDownloadTerminal);

  // Ubisoft can't spawn UPC from the backend (no gamescope session in
  // Gaming Mode → invisible window). The worker bootstraps the prefix,
  // then emits UBISOFT_INSTALL_LAUNCH_REQUESTED; we open UPC via Steam's
  // RunGame so it gets its own session and renders. The worker then
  // watches the prefix for the installed files. See ubisoftShortcutLaunch.
  useEventBus(Events.UBISOFT_INSTALL_LAUNCH_REQUESTED, (payload) => {
    const storeGameId = (payload as { store_game_id?: unknown }).store_game_id;
    if (typeof storeGameId !== "string" || !storeGameId) return;
    if (ubisoftLaunchedRef.current.has(storeGameId)) return;
    ubisoftLaunchedRef.current.add(storeGameId);
    void launchUbisoftInstallViaShortcut(storeGameId, {
      UNIFIDECK_UBISOFT_ACTION: "install",
    }).then((result) => {
      if (!result.success) {
        // Launch failed — drop the dedupe entry so a retry can fire.
        ubisoftLaunchedRef.current.delete(storeGameId);
        console.error(
          "[DownloadContext] Ubisoft UPC RunGame failed:",
          result.error,
        );
      }
    });
  });

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
      // download.id ≡ "<store>:<game_id>" — split for the
      // two-arg backend signature. If the wire ever returns a
      // bare id, fall back to ("", id) so the RPC at least
      // surfaces a clean ``not_found`` instead of a TypeError.
      const idx = downloadId.indexOf(":");
      const store = idx > 0 ? downloadId.slice(0, idx) : "";
      const gameId = idx > 0 ? downloadId.slice(idx + 1) : downloadId;
      return cancelMut.mutate(store, gameId);
    },
    [cancelMut],
  );

  const value: DownloadContextValue = {
    queue,
    loading: initial.loading,
    installGame,
    uninstallGame,
    cancelDownload,
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
