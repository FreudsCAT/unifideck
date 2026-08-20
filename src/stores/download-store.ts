/**
 * download-store — boot-time singleton for download queue state.
 *
 * Replaces the QAM-bound EventBus subscriptions and initial
 * `get_download_queue` RPC that previously lived inside
 * `<DownloadProvider>`. The store:
 *
 *   1. Fetches the initial queue at boot via `get_download_queue`.
 *   2. Subscribes to all DOWNLOAD_* events to keep the snapshot
 *      current even while the QAM is closed.
 *   3. Handles `UBISOFT_INSTALL_LAUNCH_REQUESTED` — critical for
 *      opening UPC in Gaming Mode when the QAM is closed.
 *   4. Calls `invalidateGameInfo` + `bumpGameStateVersion` on
 *      terminal download events so AppDetails shows fresh state.
 *
 * React components subscribe via `useSyncExternalStore` through
 * the thin `<DownloadProvider>` wrapper (which now only provides
 * mutation actions and the reactive snapshot).
 */
import { call, toaster } from "@decky/api";
import i18n from "i18next";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import type { EventName } from "../types/events";
import { EventBusClient } from "../api/event-bus-client";
import { invalidateGameInfo } from "../hooks/useGameInfo";
import { bumpGameStateVersion } from "../lib/game-state-version";
import { invalidateGameSize } from "../lib/game-size-cache";
import { friendlyDownloadError } from "../lib/download-errors";
import { launchUbisoftInstallViaShortcut } from "../utils/ubisoftShortcutLaunch";
import { launchBattlenetInstallViaShortcut } from "../utils/battlenetShortcutLaunch";

/**
 * Wrapper stores whose install flow needs the frontend to open their vendor
 * client. `[event, launcher, label]` — adding EA App is one more row.
 */
const WRAPPER_INSTALL_LAUNCHERS: ReadonlyArray<
  readonly [
    EventName,
    (storeGameId: string) => Promise<{ success: boolean; error?: string }>,
    string,
  ]
> = [
  [
    "ubisoft_install_launch_requested",
    (id) =>
      launchUbisoftInstallViaShortcut(id, {
        UNIFIDECK_UBISOFT_ACTION: "install",
      }),
    "Ubisoft UPC",
  ],
  [
    "battlenet_install_launch_requested",
    launchBattlenetInstallViaShortcut,
    "Battle.net",
  ],
];
import type { DownloadItem, DownloadQueueInfo } from "../types/downloads";

// ── Helpers (moved from DownloadContext) ─────────────────

/** Pull the appId out of a DOWNLOAD_* terminal event payload. */
function extractAppId(payload: unknown): number | null {
  if (!payload || typeof payload !== "object") return null;
  const game = (payload as { game?: { app_id?: unknown } }).game;
  if (!game || typeof game !== "object") return null;
  const id = (game as { app_id?: unknown }).app_id;
  return typeof id === "number" ? id : null;
}

/** Pull the failure reason + title out of a `download_failed` payload.
 *  The event carries both `item.error_message` (the folded CLI tail)
 *  and a top-level `error` — prefer the item's message, fall back to
 *  the top-level one. */
function extractFailure(payload: unknown): {
  error?: string;
  title?: string;
} {
  if (!payload || typeof payload !== "object") return {};
  const item = (
    payload as { item?: { error_message?: unknown; game_title?: unknown } }
  ).item;
  const itemError =
    item && typeof item === "object"
      ? (item as { error_message?: unknown }).error_message
      : undefined;
  const topError = (payload as { error?: unknown }).error;
  const title =
    item && typeof item === "object"
      ? (item as { game_title?: unknown }).game_title
      : undefined;
  return {
    error:
      typeof itemError === "string" && itemError
        ? itemError
        : typeof topError === "string"
        ? topError
        : undefined,
    title: typeof title === "string" ? title : undefined,
  };
}

/** Build the `"<store>:<game_id>"` key from a terminal payload. */
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

/** Normalise the backend's queue shape to the frontend DTO. */
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

// ── Snapshot type ────────────────────────────────────────

export interface DownloadSnapshot {
  queue: DownloadQueueInfo | null;
  loading: boolean;
}

// ── Store implementation ────────────────────────────────

type Listener = () => void;

class DownloadStoreImpl {
  private _snapshot: DownloadSnapshot = { queue: null, loading: true };
  private _listeners = new Set<Listener>();
  private _unsubs: (() => void)[] = [];
  private _wrapperLaunched = new Set<string>();

  /** Start subscriptions and initial fetch. */
  start(): void {
    void this._fetchQueue();

    this._unsubs.push(
      EventBusClient.subscribe("download_queued", () => this._fetchQueue()),
    );

    this._unsubs.push(
      EventBusClient.subscribe("download_started", () => {
        EventBusClient.bumpToFast();
        void this._fetchQueue();
      }),
    );

    this._unsubs.push(
      EventBusClient.subscribe("download_progress", (payload) => {
        this._setSnapshot((prev) => ({
          ...prev,
          queue: prev.queue && {
            ...prev.queue,
            current: prev.queue.current && {
              ...prev.queue.current,
              progress_percent:
                (payload.progress as number) ??
                prev.queue.current.progress_percent,
              speed_mbps:
                (payload.speed_mbps as number) ?? prev.queue.current.speed_mbps,
              eta_seconds:
                (payload.eta_seconds as number) ??
                prev.queue.current.eta_seconds,
            },
          },
        }));
      }),
    );

    const onTerminal = (payload: Record<string, unknown>) => {
      const appId = extractAppId(payload);
      if (appId != null) {
        invalidateGameInfo(appId);
        bumpGameStateVersion(appId);
        // An install/update that just ended moved the game's bytes on disk.
        // The install-state flip covers a first install; this also covers an
        // update, where `installed` never changes and nothing else would tell
        // the size caches to forget.
        invalidateGameSize(appId);
      }
      const storeGameId = extractStoreGameId(payload);
      if (storeGameId) this._wrapperLaunched.delete(storeGameId);
      void this._fetchQueue();
    };

    // A failed install used to surface only as a bare red "Failed" badge —
    // the backend already folds the real error into the payload, so toast it.
    const onFailed = (payload: Record<string, unknown>) => {
      onTerminal(payload);
      const { error, title } = extractFailure(payload);
      if (!error) return;
      const body = friendlyDownloadError(error, i18n.t.bind(i18n));
      try {
        toaster.toast({
          title: title
            ? `${i18n.t("toasts.downloadFailed")}: ${title}`
            : i18n.t("toasts.downloadFailed"),
          body,
          duration: 7500,
        });
      } catch {
        console.error(
          `[DownloadStore] install failed: ${title ?? ""} — ${body}`,
        );
      }
    };

    this._unsubs.push(
      EventBusClient.subscribe("download_complete", onTerminal),
    );
    this._unsubs.push(EventBusClient.subscribe("download_failed", onFailed));
    this._unsubs.push(
      EventBusClient.subscribe("download_cancelled", onTerminal),
    );

    // Wrapper-store installs — open the vendor client via Steam's RunGame.
    // The backend cannot spawn it: in Gaming Mode a bare subprocess has no
    // gamescope session and the window never appears.
    //
    // One subscription per store, built from a table so adding EA App is a
    // row rather than another copy of this block. The dedup set is shared
    // because the key is the full `store:game_id`.
    for (const [event, launch, label] of WRAPPER_INSTALL_LAUNCHERS) {
      this._unsubs.push(
        EventBusClient.subscribe(event, (payload) => {
          const storeGameId = (payload as { store_game_id?: unknown })
            .store_game_id;
          if (typeof storeGameId !== "string" || !storeGameId) return;
          if (this._wrapperLaunched.has(storeGameId)) return;
          this._wrapperLaunched.add(storeGameId);
          void launch(storeGameId).then((result) => {
            if (!result.success) {
              this._wrapperLaunched.delete(storeGameId);
              console.error(
                `[DownloadStore] ${label} RunGame failed:`,
                result.error,
              );
            }
          });
        }),
      );
    }
  }

  /** Stop all subscriptions. */
  stop(): void {
    for (const unsub of this._unsubs) unsub();
    this._unsubs = [];
  }

  /** Re-fetch the queue from the backend. */
  async refetch(): Promise<void> {
    await this._fetchQueue();
  }

  // ── useSyncExternalStore API ──────────────────────────

  getSnapshot = (): DownloadSnapshot => this._snapshot;

  subscribe = (listener: Listener): (() => void) => {
    this._listeners.add(listener);
    return () => this._listeners.delete(listener);
  };

  // ── Internals ─────────────────────────────────────────

  private async _fetchQueue(): Promise<void> {
    try {
      const raw = await call<[], unknown>(rpcRoutes.getDownloadQueue);
      const data = unwrapRpcEnvelope<unknown>(raw, {
        route: rpcRoutes.getDownloadQueue,
        throwing: false,
      });
      this._setSnapshot({ queue: adaptQueue(data), loading: false });
    } catch (e) {
      console.warn("[DownloadStore] fetch failed:", e);
    }
  }

  private _setSnapshot(
    update: DownloadSnapshot | ((prev: DownloadSnapshot) => DownloadSnapshot),
  ): void {
    const next = typeof update === "function" ? update(this._snapshot) : update;
    this._snapshot = next;
    this._emit();
  }

  private _emit(): void {
    for (const listener of this._listeners) listener();
  }
}

/** Singleton — started at boot from `definePlugin`. */
export const downloadStore = new DownloadStoreImpl();
