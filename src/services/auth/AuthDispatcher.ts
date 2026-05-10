/**
 * AuthDispatcher — frontend-side auth orchestrator.
 *
 * Drives the backend auth pipeline through two channels :
 *
 *   1. `dispatch_unifideck_action("unifideck://auth/<store>")`
 *      to start the flow. Backend handles the entire runtime
 *      (Steam shortcut, RunGame, OAuth/CLI/Wine specifics,
 *      cleanup) — frontend has zero shortcut logic.
 *
 *   2. The backend EventBus emits `STORE_AUTH_STARTED`,
 *      `STORE_AUTH_COMPLETE` or `STORE_AUTH_FAILED` along
 *      the way. The dispatcher subscribes and resolves a
 *      single Promise per `start()` call, so callers can
 *      `await` the auth result.
 *
 * Mutex : only one auth flow at a time. A second `start()`
 * for the same store while another is in flight returns the
 * in-flight promise ; for a different store, it rejects.
 */
import { EventBusClient } from "../../api/event-bus-client";
import { ActionVerbs } from "../../api/rpc-routes";
import { Events } from "../../types/events";
import type { StoreId, AuthResult } from "../../types/api";

const AUTH_TIMEOUT_MS = 10 * 60 * 1000;  // 10 minutes ceiling

/** Auth event payload. */
interface AuthEventPayload {
  store?: string;
  success?: boolean;
  error?: string;
  needs_2fa?: boolean;
}

/** Auth dispatcher impl. */
class AuthDispatcherImpl {

  private inflight: {
    store: StoreId;
    promise: Promise<AuthResult>;
  } | null = null;

  /** Start the auth flow for `store`. Returns a promise that
   *  resolves when the backend emits a terminal auth event
   *  (`STORE_AUTH_COMPLETE` or `STORE_AUTH_FAILED`) for that
   *  store, or rejects on timeout. */
  async start(store: StoreId): Promise<AuthResult> {
    if (this.inflight && this.inflight.store === store) {
      return this.inflight.promise;
    }

    if (this.inflight) {
      throw new Error(`Auth already in flight for ${this.inflight.store}`);
    }

    EventBusClient.bumpToFast();
    const promise = this.runFlow(store);
    this.inflight = { store, promise };
    promise.finally(() => { this.inflight = null; });
    return promise;
  }

  /**
   * Internal coroutine that owns one auth flow end-to-end :
   * registers EventBus listeners for AUTH_COMPLETE and
   * AUTH_FAILED, drives the store-specific kickoff, applies
   * the configured timeout, and disposes every listener on
   * resolve / reject / cancel — guaranteeing no leak even on
   * the error path.
   */
  private async runFlow(store: StoreId): Promise<AuthResult> {
    return new Promise<AuthResult>((resolve, reject) => {
      /** Cleanup. */
      const cleanup: Array<() => void> = [];

      /** Timer. */
      const timer = setTimeout(() => {
        for (const fn of cleanup) fn();
        reject(new Error(`auth timeout: ${store}`));
      }, AUTH_TIMEOUT_MS);

      cleanup.push(() => clearTimeout(timer));

      /** On resolved. */
      const onResolved = (result: AuthResult): void => {
        for (const fn of cleanup) fn();
        resolve(result);
      };

      cleanup.push(EventBusClient.subscribe(
        Events.STORE_AUTH_COMPLETE,
        (raw) => {
          const p = raw as AuthEventPayload;
          if (p.store !== store) return;
          onResolved({ success: true, store });
        },
      ));

      cleanup.push(EventBusClient.subscribe(
        Events.STORE_AUTH_FAILED,
        (raw) => {
          const p = raw as AuthEventPayload;
          if (p.store !== store) return;
          onResolved({
            success: false,
            store,
            error: p.error ?? "unknown auth failure",
            needs_2fa: p.needs_2fa,
          });
        },
      ));

      // Fire the URI dispatch only after the listeners are
      // installed — otherwise a fast backend flow could emit
      // its terminal event before we subscribe.
      void EventBusClient.dispatchAction(ActionVerbs.AUTH, store)
        .catch((e) => {
          for (const fn of cleanup) fn();
          reject(e);
        });
    });
  }
}
/** Singleton — auth flows are mutually exclusive by nature. */
export const AuthDispatcher = new AuthDispatcherImpl();
