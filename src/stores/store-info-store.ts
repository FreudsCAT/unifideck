/**
 * store-info-store — boot-time cache for registered store info.
 *
 * Replaces the `useRPCQuery(getStoreInfos)` call that previously
 * ran on every QAM mount inside `<StoreProvider>`. The store:
 *
 *   1. Fetches `get_store_infos` once at boot.
 *   2. Subscribes to `STORE_REGISTERED` to auto-refresh when a
 *      new store connector is hot-loaded.
 *   3. Exposes a `refetch()` for manual refresh.
 *
 * React components subscribe via `useSyncExternalStore`.
 */
import { call } from "@decky/api";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { EventBusClient } from "../api/event-bus-client";
import type { StoreInfo } from "../types/api";

// ── Snapshot type ────────────────────────────────────────

export interface StoreInfoSnapshot {
  stores: StoreInfo[];
  loading: boolean;
  error: Error | null;
}

// ── Store implementation ────────────────────────────────

type Listener = () => void;

class StoreInfoStoreImpl {
  private _snapshot: StoreInfoSnapshot = {
    stores: [],
    loading: true,
    error: null,
  };
  private _listeners = new Set<Listener>();
  private _unsubs: (() => void)[] = [];

  /** Start the store — initial fetch + STORE_REGISTERED sub. */
  start(): void {
    void this._fetch();

    this._unsubs.push(
      EventBusClient.subscribe("store_registered", () => {
        void this._fetch();
      }),
    );
  }

  /** Stop all subscriptions. */
  stop(): void {
    for (const unsub of this._unsubs) unsub();
    this._unsubs = [];
  }

  /** Re-fetch store infos from the backend. */
  async refetch(): Promise<void> {
    await this._fetch();
  }

  // ── useSyncExternalStore API ──────────────────────────

  getSnapshot = (): StoreInfoSnapshot => this._snapshot;

  subscribe = (listener: Listener): (() => void) => {
    this._listeners.add(listener);
    return () => this._listeners.delete(listener);
  };

  // ── Internals ─────────────────────────────────────────

  private async _fetch(): Promise<void> {
    try {
      const raw = await call<[], unknown>(rpcRoutes.getStoreInfos);
      const data = unwrapRpcEnvelope<StoreInfo[]>(raw, {
        route: rpcRoutes.getStoreInfos,
        throwing: false,
      });
      this._snapshot = {
        stores: Array.isArray(data) ? data : [],
        loading: false,
        error: null,
      };
      this._emit();
    } catch (e) {
      this._snapshot = {
        stores: this._snapshot.stores,
        loading: false,
        error: e instanceof Error ? e : new Error(String(e)),
      };
      this._emit();
    }
  }

  private _emit(): void {
    for (const listener of this._listeners) listener();
  }
}

/** Singleton — started at boot from `definePlugin`. */
export const storeInfoStore = new StoreInfoStoreImpl();
