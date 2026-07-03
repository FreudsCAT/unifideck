/**
 * Auth services — barrel export.
 *
 * One service : `AuthDispatcher`. It is the thin frontend
 * counterpart to the backend's auth pipeline. Backend owns
 * the entire shortcut lifecycle (Steam shortcut creation,
 * RunGame, completion detection, prefix management,
 * cleanup) ; frontend only sends a single URI verb and
 * listens to the resulting EventBus events.
 *
 * Per-store quirks (Microsoft xCloud OAuth, Ubisoft 2FA via
 * Wine prefix, Epic/GOG/Amazon CLI tokens) are 100 %
 * backend concerns.
 */
export { AuthDispatcher } from "./AuthDispatcher";
