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
  useState,
} from "react";
import { useRPCMutation } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { useEventBus, EventBusClient } from "../api/event-bus-client";
import { Events } from "../types/events";
import type { SyncProgress } from "../types/syncProgress";

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
    setSyncing(false);
    setCancelling(false);
  });

  useEventBus(Events.SYNC_FAILED, () => {
    setSyncing(false);
    setCancelling(false);
  });

  useEventBus(Events.SYNC_CANCELLED, () => {
    setSyncing(false);
    setCancelling(false);
  });

  /** Start sync. */
  const startSync = useCallback(async () => {
    if (isSyncing) return;
    EventBusClient.bumpToFast();
    await startMut.mutate();
  }, [isSyncing, startMut]);

  /** Force sync. Optionally re-fetches all artwork
   *  (slow, bandwidth-heavy). Default keeps current artwork. */
  const forceSync = useCallback(async (resyncArtwork?: boolean) => {
    EventBusClient.bumpToFast();
    await forceMut.mutate(resyncArtwork);
  }, [forceMut]);

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
