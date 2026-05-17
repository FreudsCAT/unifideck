/**
 * SyncContext — library sync state machine.
 *
 * Provides the current `SyncProgress` snapshot plus the
 * three actions a UI can trigger : start, force, cancel.
 * Listens on the EventBus for SYNC_PROGRESS and
 * SYNC_COMPLETE so the progress bar updates reactively
 * without polling.
 *
 * The lifecycle invariant : at most one sync runs at a time.
 * `startSync` while one is running is a no-op (returns the
 * current run id), so duplicate Sync button presses can't
 * stack.
 */
import React, {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { call } from "@decky/api";
import { showModal } from "@decky/ui";
import { useRPCMutation } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { useEventBus, EventBusClient } from "../api/event-bus-client";
import { Events } from "../types/events";
import type { SyncProgress } from "../types/syncProgress";
import { SteamRestartModal } from "../components/modals/SteamRestartModal";

const PROGRESS_POLL_MS = 500;

/** Sync context value. */
interface SyncContextValue {
  progress: SyncProgress | null;
  isSyncing: boolean;
  isCancelling: boolean;
  startSync: () => Promise<void>;
  forceSync: (resyncArtwork?: boolean) => Promise<void>;
  cancelSync: () => Promise<void>;
}

const Ctx = createContext<SyncContextValue | null>(null);

/**
 * Provider that owns the live sync status. Maintains
 * a polling loop on `get_sync_progress` while a sync
 * is running and tears it down on completion.
 *
 * Holding the polling lifecycle here keeps it
 * single-instance even if multiple components read
 * the progress simultaneously.
 */
export const SyncProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [progress, setProgress] = useState<SyncProgress | null>(null);
  const [isSyncing, setSyncing] = useState(false);
  const [isCancelling, setCancelling] = useState(false);
  const startMut = useRPCMutation<[], { run_id: number }>(
    rpcRoutes.syncLibraries,
  );

  const forceMut = useRPCMutation<[boolean?], { run_id: number }>(
    rpcRoutes.forceSyncLibraries,
  );

  const cancelMut = useRPCMutation<[], { ok: boolean }>(
    rpcRoutes.cancelSync,
  );

  // Wire EventBus
  useEventBus(Events.SYNC_STARTED, () => {
    setSyncing(true);
    setCancelling(false);
    EventBusClient.bumpToFast();
  });

  useEventBus(Events.SYNC_PROGRESS, (payload) => {
    setProgress(payload as unknown as SyncProgress);
  });

  useEventBus(Events.SYNC_COMPLETE, () => {
    setCancelling(false);
    // Do NOT setSyncing(false) here — metadata and artwork
    // enrichment phases start after the library fetch completes
    // and the progress bar must stay alive through them. The
    // 500ms polling loop will naturally stop when
    // `get_sync_progress` reports `syncing=false` (all phases
    // finished). Bridge to non-React listeners —
    // CollectionManager and LibraryContext re-fetch their caches
    // on this signal.
    window.dispatchEvent(new CustomEvent("unifideck-sync-completed"));
  });

  useEventBus(Events.SYNC_FAILED, () => {
    setSyncing(false);
    setCancelling(false);
  });

  useEventBus(Events.SYNC_CANCELLED, () => {
    setSyncing(false);
    setCancelling(false);
  });

  // Reconcile result — when ShortcutService finishes writing
  // shortcuts.vdf, prompt for a Steam restart if any entries
  // changed. Without restarting Steam, the in-memory shortcuts
  // list is stale and on shutdown Steam overwrites our writes.
  useEventBus(Events.SHORTCUT_RECONCILE_COMPLETE, (payload) => {
    const added = Number(payload?.added ?? 0);
    const removed = Number(payload?.removed ?? 0);
    if (added > 0 || removed > 0) {
      try {
        showModal(<SteamRestartModal reason="sync" closeModal={() => {}} />);
      } catch (e) {
        console.error("[SyncContext] showModal(SteamRestartModal) failed", e);
      }
    }
  });

  // Mount-time restore + adaptive 500ms polling fallback (mirrors
  // staging's UX). Even with the EventBus path working, polling
  // here is the load-bearing source of `progress`: SYNC_PROGRESS
  // events fire only at per-store boundaries (4 ticks per sync),
  // but the user expects a continuously-moving bar.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollOnce = useCallback(async () => {
    try {
      const raw = await call<[], unknown>(rpcRoutes.getSyncProgress);
      const data = unwrapRpcEnvelope<SyncProgress & { syncing?: boolean }>(
        raw, { route: rpcRoutes.getSyncProgress, throwing: false },
      );
      if (!data) return;
      if (typeof data.syncing === "boolean") {
        setSyncing(data.syncing);
        if (!data.syncing) setCancelling(false);
      }
      setProgress(data);
    } catch (e) {
      console.warn("[SyncContext] poll failed", e);
    }
  }, []);

  useEffect(() => {
    // On mount: ask the backend whether a sync is already in
    // flight (Quick Access re-open mid-sync). Restores isSyncing
    // and the latest progress so the UI doesn't miss the bar.
    void pollOnce();
  }, [pollOnce]);

  useEffect(() => {
    if (!isSyncing) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (pollRef.current) return;
    pollRef.current = setInterval(() => void pollOnce(), PROGRESS_POLL_MS);
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [isSyncing, pollOnce]);

  /** Start sync. Fires the RPC but does NOT await it — the
   *  backend syncs in the background and emits events / updates
   *  the progress getter. Awaiting here would freeze the UI if
   *  metadata enrichment takes longer than expected, and would
   *  also block the cooldown timer from starting. */
  const startSync = useCallback(async () => {
    if (isSyncing) return;
    EventBusClient.bumpToFast();
    setSyncing(true);
    void startMut.mutate().catch((e) =>
      console.warn("[SyncContext] startSync RPC failed", e));
    void pollOnce();
  }, [isSyncing, startMut, pollOnce]);

  /** Force sync. Optionally re-fetches all artwork
   *  (slow, bandwidth-heavy). Default keeps current artwork. */
  const forceSync = useCallback(async (resyncArtwork?: boolean) => {
    EventBusClient.bumpToFast();
    setSyncing(true);
    void forceMut.mutate(resyncArtwork).catch((e) =>
      console.warn("[SyncContext] forceSync RPC failed", e));
    void pollOnce();
  }, [forceMut, pollOnce]);

  /** Check whether cel sync. */
  const cancelSync = useCallback(async () => {
    if (!isSyncing || isCancelling) return;
    setCancelling(true);
    await cancelMut.mutate();
  }, [isSyncing, isCancelling, cancelMut]);

  const value: SyncContextValue = {
    progress, isSyncing, isCancelling,
    startSync, forceSync, cancelSync,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

/**
 * Access the SyncContext value. Throws if used
 * outside `<SyncProvider>` — a tree-wiring bug.
 *
 * @throws Error when the provider is missing.
 */
export function useSync(): SyncContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSync called outside <SyncProvider>");

  return v;
}
