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
import { call } from "@decky/api";
import { showModal } from "@decky/ui";
import { useRPCMutation } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { useEventBus, EventBusClient } from "../api/event-bus-client";
import { setSyncCooldownMs } from "../hooks/useSyncCooldown";
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

  // Tracks which post-sync phases the backend still owes us. Reset
  // on SYNC_STARTED; cleared via POST_SYNC_PHASE_CHANGED. When this
  // becomes empty after sync, we know the bar can come down even
  // if the 500ms poll hasn't caught up yet.
  const pendingPhasesRef = useRef<Set<string>>(new Set());

  // True only after SYNC_STARTED fires in this JS session. Guards
  // the Steam-restart modal against the event-bus replay path: when
  // the plugin reloads (Steam/Decky restart), the backend's event
  // buffer replays SHORTCUT_RECONCILE_COMPLETE + POST_SYNC_PHASE_CHANGED
  // from the prior session. Without this guard the modal pops every
  // time the QAM mounts after a plugin reload, even though Steam
  // has already restarted and the prompt is obsolete.
  const observedActiveSyncRef = useRef(false);

  // Wire EventBus
  useEventBus(Events.SYNC_STARTED, () => {
    observedActiveSyncRef.current = true;
    setSyncing(true);
    setCancelling(false);
    pendingPhasesRef.current = new Set(["artwork", "metadata", "proton_meta"]);
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
    // POST_SYNC_PHASE_CHANGED listener below clears it once both
    // phases report done. The 500ms polling loop is a fallback.
    // Bridge to non-React listeners — CollectionManager and
    // LibraryContext re-fetch their caches on this signal.
    window.dispatchEvent(new CustomEvent("unifideck-sync-completed"));
  });

  // Definitive post-sync completion signal. Without this, the
  // only path to `isSyncing=false` was the 500ms poll detecting
  // `syncing=false` from `get_sync_progress` — which races with
  // the lock release and routinely left the bar mid-progress.
  useEventBus(Events.POST_SYNC_PHASE_CHANGED, (payload) => {
    const phase = String((payload as Record<string, unknown>)?.phase ?? "");
    const active = Boolean(
      (payload as Record<string, unknown>)?.active ?? false,
    );
    if (active || !phase) return;
    pendingPhasesRef.current.delete(phase);
    if (pendingPhasesRef.current.size === 0) {
      setSyncing(false);
      setCancelling(false);
      // All post-sync phases done — now is the right time to
      // prompt for a Steam restart. The progress bar is at 100%,
      // artwork + metadata + compat enrichment are finished, and
      // the user can make an informed decision.
      if (pendingRestartRef.current && observedActiveSyncRef.current) {
        pendingRestartRef.current = false;
        try {
          showModal(<SteamRestartModal reason="sync" closeModal={() => {}} />);
        } catch (e) {
          console.error("[SyncContext] showModal(SteamRestartModal) failed", e);
        }
      } else if (pendingRestartRef.current) {
        // Replay path: events are from a sync that ran before this
        // JS module loaded. Clear the flag so a later in-session
        // SHORTCUT_RECONCILE_COMPLETE can re-arm it cleanly.
        pendingRestartRef.current = false;
      }
    }
  });

  useEventBus(Events.SYNC_FAILED, () => {
    setSyncing(false);
    setCancelling(false);
    pendingPhasesRef.current.clear();
  });

  useEventBus(Events.SYNC_CANCELLED, () => {
    setSyncing(false);
    setCancelling(false);
    // Drop stale progress so the UI doesn't keep showing
    // "cancelled" details forever; the next sync repopulates it.
    setProgress(null);
    pendingPhasesRef.current.clear();
  });

  // Reconcile result — when ShortcutService finishes writing
  // shortcuts.vdf, we STAGE the restart modal but DO NOT show it
  // yet. The shortcut-write fires the moment _finalize_sync returns,
  // which is before artwork / metadata / compat enrichment are done.
  // If we show the modal immediately the user sees "restart Steam"
  // while the progress bar still says "60% — downloading artwork".
  // Instead, defer to POST_SYNC_PHASE_CHANGED below, which fires
  // when the LAST phase finishes → bar at 100% → then prompt.
  const pendingRestartRef = useRef(false);
  useEventBus(Events.SHORTCUT_RECONCILE_COMPLETE, (payload) => {
    const added = Number(payload?.added ?? 0);
    const removed = Number(payload?.removed ?? 0);
    if (added > 0 || removed > 0) {
      pendingRestartRef.current = true;
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
      const data = unwrapRpcEnvelope<
        SyncProgress & { syncing?: boolean; cooldown_ms?: number }
      >(raw, { route: rpcRoutes.getSyncProgress, throwing: false });
      if (!data) return;
      if (typeof data.syncing === "boolean") {
        setSyncing(data.syncing);
        if (!data.syncing) setCancelling(false);
      }
      // Read backend-configured cooldown on mount (first poll).
      // ``get_sync_progress`` → ``get_status`` now carries
      // ``cooldown_ms`` from ``SyncService._cooldown_ms``, read
      // from ``sync.cooldown_seconds`` in user config at boot.
      // Subsequent polls re-read the same value (stable across a
      // session), so it's a cheap write that won't perturb React.
      if (typeof data.cooldown_ms === "number" && data.cooldown_ms >= 0) {
        setSyncCooldownMs(data.cooldown_ms);
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
    // Clear stale progress so the LibrarySync progress block,
    // gated on ``IN_PROGRESS_STATUSES``, doesn't stay hidden by a
    // lingering ``status="complete"`` from the previous run. The
    // first 500ms poll repopulates it with the new sync's state.
    setProgress(null);
    observedActiveSyncRef.current = true;
    setSyncing(true);
    void startMut.mutate().catch((e) =>
      console.warn("[SyncContext] startSync RPC failed", e));
    void pollOnce();
  }, [isSyncing, startMut, pollOnce]);

  /** Force sync. Optionally re-fetches all artwork
   *  (slow, bandwidth-heavy). Default keeps current artwork. */
  const forceSync = useCallback(async (resyncArtwork?: boolean) => {
    EventBusClient.bumpToFast();
    setProgress(null);
    observedActiveSyncRef.current = true;
    setSyncing(true);
    void forceMut.mutate(resyncArtwork).catch((e) =>
      console.warn("[SyncContext] forceSync RPC failed", e));
    void pollOnce();
  }, [forceMut, pollOnce]);

  /** Cancel an in-flight sync. */
  const cancelSync = useCallback(async () => {
    if (!isSyncing || isCancelling) return;
    setCancelling(true);
    // Clear stale progress immediately so the bar / counters don't
    // linger while the backend tears the sync down — visual feedback
    // that the cancel was received, even before SYNC_CANCELLED fires.
    setProgress(null);
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
