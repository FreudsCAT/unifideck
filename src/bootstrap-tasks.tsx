/**
 * Bootstrap tasks — one-shot startup work for the plugin.
 *
 * Runs in the plugin entry's `definePlugin` callback. Each
 * task is fire-and-forget — none of them block UI rendering.
 * Failure of one task never blocks the others (each runs in
 * its own try/catch).
 *
 * Tasks :
 *  - applyLanguagePreference  : reads backend language pref
 *    and switches i18next to it (default = navigator lang).
 *  - checkAccountSwitch       : detects Steam account change
 *    and shows the migration modal if needed (legacy RPCs
 *    `check_account_switch` + `migrate_account_data`).
 *  - registerLifetimeListener : global app lifetime hook for
 *    playtime tracking — calls `notify_game_launched` /
 *    `notify_game_stopped` on every Steam app start/stop.
 *
 * Replaces the ~600 lines of inline startup code from the
 * legacy index.tsx (lines 1960-1995 for the account switch
 * flow specifically).
 */
import { call } from "@decky/api";
import { showModal } from "@decky/ui";
import i18n from "i18next";
import { rpcRoutes } from "./api/rpc-routes";
import { unwrapRpcEnvelope } from "./api/useRPC";
import {
  AUTO_DETECT,
  LANG_STORAGE_KEY,
  resolveAutoLanguage,
} from "./i18n/translations";
import { AccountSwitchModal, SteamRestartModal } from "./components/modals";
import { uploadSteamOwnedTitles } from "./lib/steam-bridge/owned-library";
import type { Unregisterable } from "./types/steam";
/** Language pref — the `data` payload of `get_language_preference`
 *  after the `{success, error, data}` envelope is unwrapped.
 *  `locale` is the stored preference ("auto" or a concrete tag). */
interface LanguagePref {
  locale: string;
}
/** Account switch info. */
interface AccountSwitchInfo {
  show_modal: boolean;
  has_registry: boolean;
  has_auth_tokens: boolean;
}
/** Migrate result. */
interface MigrateResult {
  shortcuts_created: number;
  artwork_copied: number;
}
/**
 * Bootstrap task : push the persisted UI language
 * to i18next and to the backend before the rest of
 * the tree mounts, so toasts and labels are already
 * localised on the very first render.
 *
 * @returns a promise that resolves once both sinks
 *   have acknowledged the new language.
 */
export async function applyLanguagePreference(): Promise<void> {
  try {
    // get_language_preference returns the {success, error, data}
    // envelope — unwrap to the {locale} payload (raw `call` doesn't).
    const raw = await call<[], unknown>(rpcRoutes.getLanguagePreference);
    const r = unwrapRpcEnvelope<LanguagePref>(raw, {
      route: rpcRoutes.getLanguagePreference,
      throwing: false,
    });
    const pref = r?.locale || AUTO_DETECT;
    // "auto" (or an empty pref) resolves to the detected tag —
    // i18next has no "auto" bundle and would fall back to English.
    const tag = pref === AUTO_DETECT ? resolveAutoLanguage() : pref;
    if (i18n.language !== tag) {
      await i18n.changeLanguage(tag);
    }
    // Mirror the preference so the early toast path and
    // <LocaleProvider> agree on what's selected.
    try {
      localStorage.setItem(LANG_STORAGE_KEY, pref);
    } catch {
      // ignore quota/availability errors
    }
  } catch {
    // Backend not ready — safe default (navigator language) stays
  }
}
/**
 * Bootstrap task : compare the active Steam user
 * with the one Unifideck last ran under. On
 * mismatch, emit `ACCOUNT_SWITCHED` and clear
 * cross-account caches so we don't leak the previous
 * user's library / artwork.
 *
 * @returns a promise resolving once the check (and
 *   any required cache clear) is done.
 */
export async function checkAccountSwitch(): Promise<void> {
  try {
    const r = await call<[], AccountSwitchInfo>(rpcRoutes.checkAccountSwitch);
    if (!r?.show_modal) return;
    showModal(
      <AccountSwitchModal
        hasRegistry={r.has_registry}
        hasAuthTokens={r.has_auth_tokens}
        onMigrate={async () => {
          await call<[], MigrateResult>(rpcRoutes.migrateAccountData);
          showModal(<SteamRestartModal />);
        }}
        onClearAuths={async () => {
          await call<[], unknown>(rpcRoutes.clearStoreAuths);
        }}
        onSkip={() => {}}
        closeModal={() => {}}
      />,
    );
  } catch {
    // Silently ignore — never block plugin load
  }
}
/**
 * Bootstrap task : install the singleton Steam
 * lifetime listener that drives the Unifideck game
 * runner. Returns the disposer used by
 * `runTeardown` (OP-79) on plugin shutdown.
 *
 * @returns a disposer that detaches the listener.
 */
export function registerLifetimeListener(): Unregisterable | null {
  try {
    return (
      window.SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(
        (n) => onAppLifetime(n),
      ) ?? null
    );
  } catch (e) {
    console.error("[Bootstrap] lifetime listener registration failed:", e);
    return null;
  }
}
/** On app lifetime. */
function onAppLifetime(n: {
  unAppID: number;
  bRunning: boolean;
  nInstanceID: number;
}): void {
  if (n.bRunning) {
    void call(rpcRoutes.notifyGameLaunched, n.unAppID).catch(() => {});
  } else {
    void call(rpcRoutes.notifyGameStopped, n.unAppID).catch(() => {});
  }
}
/**
 * Bootstrap task : sweep any persistent OAuth auth
 * shortcut left over in Steam's in-memory app store by
 * a previous plugin version. Today's auth flow only
 * uses ephemeral shortcuts (created and removed in one
 * connect cycle); a stale persistent row would otherwise
 * keep launching with cover art + a real-game tile
 * instead of the unifideck-launcher tile. Idempotent —
 * RemoveShortcut on an unknown appid is a no-op.
 */
export function purgeLeftoverAuthShortcuts(): void {
  try {
    const appStore = (
      window as unknown as {
        appStore?: {
          m_mapApps?: {
            forEach?: (
              cb: (
                app: { LaunchOptions?: unknown; launch_options?: unknown },
                id: number,
              ) => void,
            ) => void;
          };
        };
      }
    ).appStore;
    const map = appStore?.m_mapApps;
    if (!map?.forEach) return;
    const stalePrefixes = [
      "epic:epic-auth",
      "gog:gog-auth",
      "amazon:amazon-auth",
      "microsoft:ms-auth",
    ];
    const victims: number[] = [];
    map.forEach((app, appId) => {
      const lo = app?.LaunchOptions ?? app?.launch_options;
      if (typeof lo !== "string") return;
      if (stalePrefixes.some((p) => lo.startsWith(p))) {
        victims.push(appId);
      }
    });
    const steamApps = window.SteamClient?.Apps;
    if (!steamApps?.RemoveShortcut) return;
    for (const appId of victims) {
      console.log(
        `[Bootstrap] Removing leftover persistent auth shortcut appId=${appId}`,
      );
      try {
        steamApps.RemoveShortcut(appId);
      } catch (e) {
        console.error(`[Bootstrap] RemoveShortcut(${appId}) failed:`, e);
      }
    }
  } catch (e) {
    console.error("[Bootstrap] purgeLeftoverAuthShortcuts failed:", e);
  }
}
/** Run all bootstrap tasks concurrently. Returns the
 *  unregister handle for the lifetime listener so the
 *  plugin entry can call it on unload. */
export async function runBootstrapTasks(): Promise<Unregisterable | null> {
  purgeLeftoverAuthShortcuts();
  const [, , , listener] = await Promise.all([
    applyLanguagePreference(),
    checkAccountSwitch(),
    // Seed the owned-Steam-library snapshot early so a backend-triggered
    // (auto) sync can hide Steam-linked Ubisoft games before the user's
    // first manual sync. Best effort; refreshed again before each sync.
    uploadSteamOwnedTitles(),
    Promise.resolve(registerLifetimeListener()),
  ]);
  return listener;
}
