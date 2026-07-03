/**
 * Ambient declaration for Decky Loader's global websocket router.
 *
 * `window.DeckyBackend` is created by Decky Loader itself
 * (`window.DeckyBackend = new WSRouter()`) and is the ONLY way to reach
 * loader-level routes such as `utilities/install_plugin` and
 * `utilities/confirm_plugin_install`, plus loader lifecycle events
 * (`loader/plugin_download_start|info|finish`, `loader/add_plugin_install_prompt`).
 *
 * This is distinct from `call`/`callable` in `@decky/api`, which are
 * plugin-scoped and route to the plugin's own Python backend — calling a
 * `utilities/*` route through them throws
 * `'Plugin' object has no attribute 'utilities/install_plugin'`.
 */
export {};

declare global {
  interface DeckyBackendRouter {
    /** Call a loader route and await its reply. */
    call<Return = unknown, Args extends unknown[] = unknown[]>(
      route: string,
      ...args: Args
    ): Promise<Return>;
    // Loader events carry arbitrary, per-event argument shapes; callers pass
    // precisely-typed listeners (e.g. `(name: string) => void`). `unknown[]`
    // rest params reject those under strict function-type checking, so the
    // event-listener boundary is intentionally `any[]`.
    /* eslint-disable @typescript-eslint/no-explicit-any */
    /** Subscribe to a loader event; returns the listener for symmetry. */
    addEventListener(
      event: string,
      listener: (...args: any[]) => void,
    ): (...args: any[]) => void;
    /** Unsubscribe a previously registered listener. */
    removeEventListener(
      event: string,
      listener: (...args: any[]) => void,
    ): void;
    /* eslint-enable @typescript-eslint/no-explicit-any */
  }

  interface Window {
    DeckyBackend?: DeckyBackendRouter;
  }
}
