/**
 * AuthDispatcher — frontend-side auth orchestrator.
 *
 * Owns the full per-store auth handshake end-to-end :
 *
 *   1. ``store_auth(store, "start")`` on the backend so it
 *      creates/refreshes the auth shortcut, writes its
 *      auth-URL file, starts its session monitor, etc.
 *   2. ``launch<Store>AuthViaShortcut()`` so the user actually
 *      sees the browser / launcher (Steam's ``RunGame()``).
 *   3. Subscribes to the backend EventBus
 *      (``STORE_AUTH_COMPLETE`` / ``STORE_AUTH_FAILED``) so
 *      callers can `await` the final outcome.
 *
 * Components / hooks call ``AuthDispatcher.start(store)`` and
 * get a single Promise back. The legacy
 * ``dispatch_unifideck_action("unifideck://auth/<store>")``
 * channel is kept as the way the *backend* talks to itself
 * from toast-action buttons ; UI-initiated connects use the
 * direct ``store_auth`` RPC because that's the only path that
 * returns the per-store ``AuthResult`` we toast on completion.
 *
 * Mutex : only one auth flow at a time. A second `start()`
 * for the same store while another is in flight returns the
 * in-flight promise ; for a different store, it rejects.
 */
import { call } from "@decky/api";
import { EventBusClient } from "../../api/event-bus-client";
import { rpcRoutes } from "../../api/rpc-routes";
import { unwrapRpcEnvelope } from "../../api/useRPC";
import { Events } from "../../types/events";
import {
  launchAmazonAuthViaShortcut,
  launchEpicAuthViaShortcut,
  launchGogAuthViaShortcut,
  launchMicrosoftAuthViaShortcut,
} from "../../utils/authShortcutLaunch";
import { launchUbisoftAuthViaShortcut } from "../../utils/ubisoftShortcutLaunch";
import type { StoreId, AuthResult } from "../../types/api";

const AUTH_TIMEOUT_MS = 10 * 60 * 1000;  // 10 minutes ceiling

/** Auth event payload. */
interface AuthEventPayload {
  store?: string;
  success?: boolean;
  error?: string;
  needs_2fa?: boolean;
}

/** Backend `store_auth` envelope (unwrapped by useRPC for hook
 *  callers ; we call `call()` directly here, so the wrapper
 *  envelope is left intact and unwrapped manually). */
interface StoreAuthResponse {
  success?: boolean;
  data?: AuthResult;
  url?: string;
  error?: string;
}

/** Auth dispatcher impl. */
class AuthDispatcherImpl {

  private inflight: {
    store: StoreId;
    promise: Promise<AuthResult>;
  } | null = null;

  /** Start the auth flow for `store`. Resolves when the
   *  backend emits `STORE_AUTH_COMPLETE` / `STORE_AUTH_FAILED`
   *  for that store, or rejects on timeout / shortcut launch
   *  failure. */
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
   *  - subscribe to STORE_AUTH_COMPLETE / STORE_AUTH_FAILED
   *  - kick the backend `store_auth` RPC
   *  - launch the auth shortcut so the user sees the flow
   *  - resolve / reject + dispose every listener.
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

      // Fire the kick + shortcut launch only after the
      // listeners are installed — otherwise a fast backend
      // flow could emit its terminal event before we
      // subscribe.
      void this.kickAndLaunch(store).catch((e) => {
        for (const fn of cleanup) fn();
        reject(e);
      });
    });
  }

  /** Two-stage kick : backend prep then frontend shortcut
   *  launch. Throws if either stage fails. */
  private async kickAndLaunch(store: StoreId): Promise<void> {
    console.log(`[AuthDispatcher:${store}] backend prep via store_auth`);
    const raw = await call<[StoreId, string], unknown>(
      rpcRoutes.storeAuth, store, "start",
    );
    const startResult = unwrapRpcEnvelope<StoreAuthResponse>(raw, {
      route: rpcRoutes.storeAuth, throwing: false,
    });
    console.log(
      `[AuthDispatcher:${store}] store_auth returned:`, startResult,
    );
    console.log(`[AuthDispatcher:${store}] launching shortcut`);
    const launchResult = await this.launchForStore(store);
    console.log(
      `[AuthDispatcher:${store}] shortcut launch result:`, launchResult,
    );
    if (!launchResult.success) {
      throw new Error(
        launchResult.error ?? `${store} auth shortcut failed to launch`,
      );
    }
  }

  /** Dispatch to the per-store shortcut launcher. */
  private async launchForStore(
    store: StoreId,
  ): Promise<{ success: boolean; error?: string }> {
    switch (store) {
      case "epic":      return launchEpicAuthViaShortcut();
      case "gog":       return launchGogAuthViaShortcut();
      case "amazon":    return launchAmazonAuthViaShortcut();
      case "microsoft": return launchMicrosoftAuthViaShortcut();
      case "ubisoft":   return launchUbisoftAuthViaShortcut();
      default:
        return { success: false, error: `no launcher wired for ${store}` };
    }
  }
}
/** Singleton — auth flows are mutually exclusive by nature. */
export const AuthDispatcher = new AuthDispatcherImpl();
