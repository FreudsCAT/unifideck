/**
 * Shared shortcut launch primitives.
 *
 * Types and helpers consumed by every store auth flow that
 * goes through Steam's RunGame API. Lives inside
 * SteamBridge because all of them poke at Steam internals
 * (`window.appStore.m_mapApps`, `window.SteamClient.Apps`)
 * — the same globals the rest of SteamBridge isolates.
 *
 * Centralising these primitives lets the auth launcher
 * (utils/authShortcutLaunch.ts) and the Ubisoft-specific
 * launcher (utils/ubisoftShortcutLaunch.ts) share a single
 * source of truth for context shape, return shape, and the
 * helpers that read app-store entries.
 *
 * Anti-pattern explicitly avoided : duplicating the
 * `ShortcutLaunchContext` interface inside each launcher,
 * which led to silent drift in the legacy code (Microsoft
 * launcher exported its own subtly different result type).
 */

/** Shape returned by the backend for any auth shortcut
 *  context RPC (`get_<store>_auth_shortcut_context`). */
export type ShortcutLaunchContext = {
  success: boolean;
  store_game_id?: string;
  tool_name?: string;
  appid_unsigned?: number;
  launch_wait_ms?: number;
  is_linux_runtime?: boolean;
  /** True when `tool_name` equals Steam's global default
   *  (`CompatToolMapping["0"]`) — a distro/system default (e.g.
   *  Bazzite's "Proton-CachyOS Latest") rather than an explicit
   *  per-game Force-Compat choice, so it must not be adopted as a
   *  per-game Proton override. */
  is_global_default?: boolean;
  launcher_path?: string;
  current_launch_options?: string;
  saved_proton_tool?: string;
  error?: string;
};

/** Common result shape every shortcut launcher resolves. */
export type ShortcutLaunchResult = {
  success: boolean;
  already_running?: boolean;
  /** Appid Steam was actually asked to run, when one was resolved.
   *  Callers that must know when the launched app *ends* — the auth
   *  flows, which otherwise wait on a backend event that may never
   *  arrive — pass it to {@link watchAppStopped}. */
  app_id?: number;
  error?: string;
};

/**
 * Call `onStopped` once Steam reports `appId` has stopped, having first
 * seen it running. Returns an unsubscribe function.
 *
 * The `sawRunning` edge matters: Steam emits `bRunning: false` for apps
 * that were never started in this session, so acting on a bare "not
 * running" notification fires immediately and means nothing.
 *
 * Shared because two callers need the identical edge — temp-shortcut
 * cleanup (removing the entry early kills the app's XWayland session and
 * the login window with it) and the auth dispatcher (a sign-in whose
 * client has exited must not leave the Sign In button wedged).
 */
export function watchAppStopped(
  appId: number,
  onStopped: () => void,
): () => void {
  let sawRunning = false;
  const sub =
    window.SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(
      (n) => {
        if (n.unAppID !== appId) return;
        if (n.bRunning) {
          sawRunning = true;
          return;
        }
        if (sawRunning) onStopped();
      },
    );
  return () => sub?.unregister();
}

/** App store entry. */
interface AppStoreEntry {
  gameid?: unknown;
  local_per_client_data?: { display_status?: unknown };
  per_client_data?: Array<{ display_status?: unknown } | undefined>;
}

/** App store shape. */
interface AppStoreShape {
  m_mapApps?: {
    get?: (id: number) => AppStoreEntry | undefined;
  };
}

/** Get app store entry. */
function getAppStoreEntry(appId: number): AppStoreEntry | undefined {
  const appStore = (window as unknown as { appStore?: AppStoreShape }).appStore;
  return appStore?.m_mapApps?.get?.(appId);
}

/** Resolve the canonical RunGame id for a Steam shortcut.
 *  Falls back to a stringified appId if Steam hasn't filled
 *  in the gameid yet. */
export function getShortcutRunGameId(appId: number): string {
  const entry = getAppStoreEntry(appId);
  const gameId = entry?.gameid;

  if (typeof gameId === "string" && gameId.length > 0) {
    return gameId;
  }
  try {
    const val = (BigInt(appId) << 32n) | 0x02000000n;
    return val.toString();
  } catch {
    return String(appId);
  }
}

/** Read Steam's display_status for a shortcut, with a
 *  fallback to per-client data when local data is empty.
 *  Returns undefined if the shortcut is not in Steam's
 *  in-memory app store. Status values are Steam-internal
 *  (no public enum) — observed values include
 *  1 = launching, 4 = running. */
function getShortcutDisplayStatus(appId: number): number | undefined {
  const entry = getAppStoreEntry(appId);
  if (!entry) return undefined;

  const local = entry.local_per_client_data?.display_status;
  if (typeof local === "number") return local;

  const perClient = entry.per_client_data?.[0]?.display_status;

  return typeof perClient === "number" ? perClient : undefined;
}

/** Best-effort check : true if Steam's app-store reports the
 *  shortcut as currently running or launching. Used by every
 *  shortcut launcher to skip a redundant RunGame() call when
 *  the user re-clicks Sign-In or Play. */
export function isShortcutAppRunning(appId: number): boolean {
  const status = getShortcutDisplayStatus(appId);
  return status === 1 || status === 4;
}
