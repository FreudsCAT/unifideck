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

const AUTH_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes ceiling

/** Auth event payload. */
interface AuthEventPayload {
  store?: string;
  success?: boolean;
  error?: string;
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
  /** Per-store in-flight auth: allows concurrent auth for
   *  DIFFERENT stores (e.g. user starts GOG then clicks
   *  Microsoft — both run in parallel). Only deduplicates
   *  the SAME store (clicking Connect twice returns the
   *  existing promise). */
  private inflight = new Map<StoreId, Promise<AuthResult>>();

  /** Start the auth flow for `store`. Resolves when the
   *  backend emits `STORE_AUTH_COMPLETE` / `STORE_AUTH_FAILED`
   *  for that store, or rejects on timeout / shortcut launch
   *  failure. */
  async start(store: StoreId): Promise<AuthResult> {
    const existing = this.inflight.get(store);
    if (existing) return existing;

    EventBusClient.bumpToFast();
    const promise = this.runFlow(store);
    this.inflight.set(store, promise);
    promise.finally(() => {
      this.inflight.delete(store);
    });
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
        if (result.success) {
          // Fire-and-forget: a fresh login should make the store
          // available for sync immediately, not just after the next
          // restart. Queues behind an in-flight sync on the backend
          // (SyncService._enqueue) rather than blocking this Promise —
          // callers resolve as soon as auth completes, same as before.
          void call<[StoreId], unknown>(rpcRoutes.requestAuthSync, store).catch(
            (e) => {
              console.error(
                `[AuthDispatcher:${store}] requestAuthSync failed:`,
                e,
              );
            },
          );
        }
        resolve(result);
      };

      cleanup.push(
        EventBusClient.subscribe(Events.STORE_AUTH_COMPLETE, (raw) => {
          const p = raw as AuthEventPayload;
          if (p.store !== store) return;
          onResolved({ success: true, store });
        }),
      );

      cleanup.push(
        EventBusClient.subscribe(Events.STORE_AUTH_FAILED, (raw) => {
          const p = raw as AuthEventPayload;
          if (p.store !== store) return;
          onResolved({
            success: false,
            store,
            error: p.error ?? "unknown auth failure",
          });
        }),
      );

      // Fire the kick + shortcut launch only after the
      // listeners are installed — otherwise a fast backend
      // flow could emit its terminal event before we
      // subscribe.
      void this.kickAndLaunch(store)
        .then((early) => {
          // Fast-path : the backend's ``store_auth`` returned
          // ``success: true`` right away (already-authed user).
          // Don't wait for an EventBus echo — the event may
          // race the RPC response and arrive before our poll
          // tick, leaving the Promise hung. Resolve directly.
          if (early) onResolved(early);
        })
        .catch((e) => {
          for (const fn of cleanup) fn();
          reject(e);
        });
    });
  }

  /** Two-stage kick : backend prep then frontend shortcut
   *  launch.
   *
   *  Returns an ``AuthResult`` only on the **fast path** —
   *  i.e. when ``store_auth`` reports ``success: true``
   *  immediately (the store's fast-path detected existing
   *  valid tokens). In that case the caller resolves the
   *  Promise directly with the returned result : the
   *  backend's ``STORE_AUTH_COMPLETE`` event may race the
   *  RPC response (the EventBus polls every 250-2000ms), so
   *  waiting for it would hang the UI at "Working…".
   *
   *  Returns ``null`` on the slow path — the shortcut has
   *  been kicked, and the surrounding ``runFlow`` waits for
   *  ``STORE_AUTH_COMPLETE`` / ``STORE_AUTH_FAILED`` events
   *  to resolve.
   *
   *  Throws if either stage fails outright (so ``runFlow``
   *  rejects the Promise).
   */
  private async kickAndLaunch(store: StoreId): Promise<AuthResult | null> {
    console.log(`[AuthDispatcher:${store}] backend prep via store_auth`);
    const raw = await call<[StoreId, string], unknown>(
      rpcRoutes.storeAuth,
      store,
      "start",
    );
    const startResult = unwrapRpcEnvelope<StoreAuthResponse>(raw, {
      route: rpcRoutes.storeAuth,
      throwing: false,
    });
    console.log(`[AuthDispatcher:${store}] store_auth returned:`, startResult);
    if (
      startResult?.success === true &&
      !(startResult as { metadata?: { pending?: boolean } } | null)?.metadata
        ?.pending
    ) {
      console.log(
        `[AuthDispatcher:${store}] backend reports already-authed, ` +
          `resolving without shortcut launch`,
      );
      return { success: true, store };
    }
    // Backend reported a structured failure (e.g. ``edge_not_installed``)
    // before any shortcut was needed. Surface it as the resolved
    // AuthResult so the caller (useStoreAuth) can react — show the
    // Chromium install modal, surface a toast, etc. — instead of
    // firing a useless shortcut launch the user can't complete.
    if (startResult && startResult.success === false && startResult.error) {
      console.log(
        `[AuthDispatcher:${store}] backend rejected start: ` +
          `${startResult.error} — skipping shortcut launch`,
      );
      return {
        success: false,
        store,
        error: startResult.error,
      } as AuthResult;
    }
    console.log(`[AuthDispatcher:${store}] launching shortcut`);
    const launchResult = await this.launchForStore(store);
    console.log(
      `[AuthDispatcher:${store}] shortcut launch result:`,
      launchResult,
    );
    if (!launchResult.success) {
      throw new Error(
        launchResult.error ?? `${store} auth shortcut failed to launch`,
      );
    }
    // Slow path : shortcut launched, wait for the backend's
    // terminal event to land on the EventBus.
    return null;
  }

  /** Dispatch to the per-store shortcut launcher. */
  private async launchForStore(
    store: StoreId,
  ): Promise<{ success: boolean; error?: string }> {
    switch (store) {
      case "epic":
        return launchEpicAuthViaShortcut();
      case "gog":
        return launchGogAuthViaShortcut();
      case "amazon":
        return launchAmazonAuthViaShortcut();
      case "microsoft":
        return launchMicrosoftAuthViaShortcut();
      case "ubisoft":
        return launchUbisoftAuthViaShortcut();
      default:
        return { success: false, error: `no launcher wired for ${store}` };
    }
  }
}
/** Singleton — auth flows are mutually exclusive by nature. */
export const AuthDispatcher = new AuthDispatcherImpl();
