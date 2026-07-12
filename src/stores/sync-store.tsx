/**
 * sync-store — boot-time singleton for library sync state.
 *
 * Replaces the QAM-bound EventBus subscriptions and 500ms polling
 * loop that previously lived inside `<SyncProvider>`. The store:
 *
 *   1. Subscribes to SYNC_* events at boot so state transitions
 *      are never missed (even with QAM closed mid-sync).
 *   2. Runs the adaptive 500ms `get_sync_progress` poll while a
 *      sync is in flight.
 *   3. Manages the deferred Steam-restart modal (staged on
 *      SHORTCUT_RECONCILE_COMPLETE, shown after all post-sync
 *      phases complete).
 *   4. Dispatches `unifideck-sync-completed` custom events for
 *      CollectionManager and LibraryContext.
 *
 * React components subscribe via `useSyncExternalStore` through
 * the thin `<SyncProvider>` wrapper.
 */
import { call } from "@decky/api";
import { showModal } from "@decky/ui";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { EventBusClient } from "../api/event-bus-client";
import { setSyncCooldownMs } from "../hooks/useSyncCooldown";
import type { SyncProgress } from "../types/syncProgress";
import { SteamRestartModal } from "../components/modals/SteamRestartModal";

const PROGRESS_POLL_MS = 500;

// ── Snapshot type ────────────────────────────────────────

export interface SyncSnapshot {
  progress: SyncProgress | null;
  isSyncing: boolean;
  isCancelling: boolean;
}

// ── Store implementation ────────────────────────────────

type Listener = () => void;

class SyncStoreImpl {
  private _snapshot: SyncSnapshot = {
    progress: null,
    isSyncing: false,
    isCancelling: false,
  };
  private _listeners = new Set<Listener>();
  private _unsubs: (() => void)[] = [];
  private _pollTimer: ReturnType<typeof setInterval> | null = null;
  private _pendingPhases = new Set<string>();
  private _observedActiveSync = false;
  private _pendingRestart = false;

  /** Start subscriptions and initial state restore. */
  start(): void {
    // Restore state — a sync may already be in flight from
    // before the frontend loaded.
    void this._pollOnce();

    this._unsubs.push(
      EventBusClient.subscribe("sync_started", (payload) => {
        this._observedActiveSync = true;
        // Seed the phase set from the backend's authoritative
        // ``registered_phases`` rather than a hardcoded list. The old
        // hardcoded {artwork,metadata,proton_meta} over-counted when the
        // real set was smaller (no CompatibilityService → no proton_meta),
        // so the set never drained and the Steam-restart modal never fired
        // (UD-006). Fall back to the legacy set for replay/older backends.
        // An artwork skip is drained by its own phase-done event.
        const phases = (payload as { registered_phases?: string[] })
          ?.registered_phases;
        this._pendingPhases = new Set(
          Array.isArray(phases) && phases.length
            ? phases
            : ["artwork", "metadata", "proton_meta"],
        );
        this._update({ isSyncing: true, isCancelling: false });
        EventBusClient.bumpToFast();
        this._startPolling();
      }),
    );

    this._unsubs.push(
      EventBusClient.subscribe("sync_progress", (payload) => {
        this._update({ progress: payload as unknown as SyncProgress });
      }),
    );

    this._unsubs.push(
      EventBusClient.subscribe("sync_complete", () => {
        this._update({ isCancelling: false });
        // Do NOT set isSyncing=false here — wait for post-sync phases.
        window.dispatchEvent(new CustomEvent("unifideck-sync-completed"));
      }),
    );

    this._unsubs.push(
      EventBusClient.subscribe("post_sync_phase_changed", (payload) => {
        const phase = String((payload as Record<string, unknown>)?.phase ?? "");
        const active = Boolean(
          (payload as Record<string, unknown>)?.active ?? false,
        );
        if (active || !phase) return;
        this._pendingPhases.delete(phase);
        if (this._pendingPhases.size === 0) {
          this._update({ isSyncing: false, isCancelling: false });
          this._stopPolling();
          if (this._pendingRestart && this._observedActiveSync) {
            this._pendingRestart = false;
            try {
              showModal(
                <SteamRestartModal reason="sync" closeModal={() => {}} />,
              );
            } catch (e) {
              console.error(
                "[SyncStore] showModal(SteamRestartModal) failed",
                e,
              );
            }
          } else if (this._pendingRestart) {
            // Replay path: clear flag so a later sync can re-arm.
            this._pendingRestart = false;
          }
        }
      }),
    );

    this._unsubs.push(
      EventBusClient.subscribe("sync_failed", () => {
        this._pendingPhases.clear();
        this._update({ isSyncing: false, isCancelling: false });
        this._stopPolling();
      }),
    );

    this._unsubs.push(
      EventBusClient.subscribe("sync_cancelled", () => {
        this._pendingPhases.clear();
        this._update({
          isSyncing: false,
          isCancelling: false,
          progress: null,
        });
        this._stopPolling();
      }),
    );

    this._unsubs.push(
      EventBusClient.subscribe("shortcut_reconcile_complete", (payload) => {
        const added = Number(payload?.added ?? 0);
        const removed = Number(payload?.removed ?? 0);
        if (added > 0 || removed > 0) {
          this._pendingRestart = true;
        }
      }),
    );
  }

  /** Stop all subscriptions and polling. */
  stop(): void {
    this._stopPolling();
    for (const unsub of this._unsubs) unsub();
    this._unsubs = [];
  }

  /** Notify the store that a sync was started by the user
   *  (called from SyncContext before the RPC). */
  notifySyncStarted(): void {
    this._observedActiveSync = true;
    this._update({
      isSyncing: true,
      isCancelling: false,
      progress: null,
    });
    EventBusClient.bumpToFast();
    this._startPolling();
    void this._pollOnce();
  }

  /** Notify the store that a cancel was requested. */
  notifyCancelRequested(): void {
    this._update({ isCancelling: true, progress: null });
  }

  // ── useSyncExternalStore API ──────────────────────────

  getSnapshot = (): SyncSnapshot => this._snapshot;

  subscribe = (listener: Listener): (() => void) => {
    this._listeners.add(listener);
    return () => this._listeners.delete(listener);
  };

  // ── Internals ─────────────────────────────────────────

  private async _pollOnce(): Promise<void> {
    try {
      const raw = await call<[], unknown>(rpcRoutes.getSyncProgress);
      const data = unwrapRpcEnvelope<
        SyncProgress & { syncing?: boolean; cooldown_ms?: number }
      >(raw, { route: rpcRoutes.getSyncProgress, throwing: false });
      if (!data) return;

      const partial: Partial<SyncSnapshot> = { progress: data };
      if (typeof data.syncing === "boolean") {
        partial.isSyncing = data.syncing;
        if (data.syncing) {
          // A sync is in flight (e.g. restored at boot, or a
          // background/scheduled run). Keep the 500ms loop alive so
          // progress refreshes — we no longer rely on a replayed
          // ``sync_started`` to start polling (those are primed past
          // on reload). ``get_status`` reports syncing through the
          // post-sync phases too, so this also self-clears the bar
          // (status → "complete") when the run actually finishes.
          this._startPolling();
        } else {
          partial.isCancelling = false;
          this._stopPolling();
        }
      }
      if (typeof data.cooldown_ms === "number" && data.cooldown_ms >= 0) {
        setSyncCooldownMs(data.cooldown_ms);
      }
      this._update(partial);
    } catch (e) {
      console.warn("[SyncStore] poll failed", e);
    }
  }

  private _startPolling(): void {
    if (this._pollTimer) return;
    this._pollTimer = setInterval(
      () => void this._pollOnce(),
      PROGRESS_POLL_MS,
    );
  }

  private _stopPolling(): void {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  private _update(partial: Partial<SyncSnapshot>): void {
    this._snapshot = { ...this._snapshot, ...partial };
    this._emit();
  }

  private _emit(): void {
    for (const listener of this._listeners) listener();
  }
}

/** Singleton — started at boot from `definePlugin`. */
export const syncStore = new SyncStoreImpl();
