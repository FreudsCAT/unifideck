/**
 * update-store — which installed games have a pending store update.
 *
 * One source of truth for every surface that needs to answer "is this
 * game out of date?": the App-Details Play section, the QAM Downloads
 * tab's Installed list, and the pre-launch warning. Before this, only
 * App-Details asked, and it asked by running the store's bulk scan
 * inline — 5-10 s for Epic, because `legendary list-installed
 * --check-updates` logs in and re-downloads the asset manifest before
 * printing anything. The Update button arrived well after the user had
 * had time to press Play.
 *
 * The scan now lives in the backend's `UpdateSweepService` (boot, after
 * each library sync, then every 6 h). This store just mirrors the result:
 *
 *   1. hydrate once from `get_available_updates` (cache-only, instant);
 *   2. stay live via `GAME_UPDATE_AVAILABLE`, which the sweep emits
 *      whenever a store's set actually changes.
 *
 * Lazily started — the first `subscribe` hydrates and wires the event.
 * Nothing polls, and nothing runs for a user who never opens a game.
 */
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { EventBusClient } from "../api/event-bus-client";
import { Events } from "../types/events";
import { call } from "@decky/api";

/** `store` → the store-native game ids with a pending update. */
type UpdateMap = Record<string, string[]>;

type Listener = () => void;

/** Payload of a `game_update_available` emission. */
interface UpdateEvent {
  store?: unknown;
  game_ids?: unknown;
}

function parseEvent(payload: unknown): { store: string; ids: string[] } | null {
  if (!payload || typeof payload !== "object") return null;
  const { store, game_ids: ids } = payload as UpdateEvent;
  if (typeof store !== "string" || !store) return null;
  if (!Array.isArray(ids)) return null;
  return { store, ids: ids.filter((v): v is string => typeof v === "string") };
}

class UpdateStoreImpl {
  private _snapshot: UpdateMap = {};
  private _listeners = new Set<Listener>();
  private _unsub: (() => void) | null = null;
  private _hydrated = false;

  // ── useSyncExternalStore API ──────────────────────────

  getSnapshot = (): UpdateMap => this._snapshot;

  subscribe = (listener: Listener): (() => void) => {
    this._listeners.add(listener);
    this._start();
    return () => {
      this._listeners.delete(listener);
    };
  };

  // ── Queries ───────────────────────────────────────────

  /** True when the sweep has seen a pending update for this game.
   *
   *  False also covers "not swept yet" — the caller cannot tell the two
   *  apart, and deliberately so: the safe default while we don't know is
   *  to offer Play, not to nag. The event flips it the moment we learn
   *  otherwise. */
  hasUpdate = (
    store: string | undefined,
    gameId: string | undefined,
  ): boolean => {
    if (!store || !gameId) return false;
    return (this._snapshot[store] ?? []).includes(gameId);
  };

  // ── Mutations ─────────────────────────────────────────

  /** Drop a game locally after its update has been queued.
   *
   *  The backend invalidates its own cache on `update_game`, but the
   *  re-scan is asynchronous; without this the button would keep saying
   *  Update until the next sweep landed. */
  clearGame = (store: string, gameId: string): void => {
    const ids = this._snapshot[store];
    if (!ids?.includes(gameId)) return;
    this._snapshot = {
      ...this._snapshot,
      [store]: ids.filter((id) => id !== gameId),
    };
    this._emit();
  };

  /** Re-read the backend's cached map. Safe to call often — the RPC
   *  never scans, it only reports what the sweep already found. */
  refresh = async (): Promise<void> => {
    try {
      const raw = await call<[], unknown>(rpcRoutes.getAvailableUpdates);
      const map = unwrapRpcEnvelope<UpdateMap | null>(raw, {
        route: rpcRoutes.getAvailableUpdates,
        throwing: false,
      });
      if (!map || typeof map !== "object") return;
      this._snapshot = map;
      this._emit();
    } catch {
      /* Non-critical: the button just stays on Play. */
    }
  };

  // ── Internals ─────────────────────────────────────────

  private _start(): void {
    if (this._unsub) return;
    this._unsub = EventBusClient.subscribe(
      Events.GAME_UPDATE_AVAILABLE,
      (payload) => this._onEvent(payload),
    );
    if (!this._hydrated) {
      this._hydrated = true;
      void this.refresh();
    }
  }

  private _onEvent(payload: unknown): void {
    const parsed = parseEvent(payload);
    if (!parsed) return;
    // Replace the store's list wholesale rather than merging: the sweep
    // emits the store's COMPLETE current set, so an id that vanished
    // (the update was applied) has to disappear here too.
    this._snapshot = { ...this._snapshot, [parsed.store]: parsed.ids };
    this._emit();
  }

  private _emit(): void {
    this._listeners.forEach((l) => l());
  }
}

/** Singleton — update state is process-global by nature. */
export const UpdateStore = new UpdateStoreImpl();
