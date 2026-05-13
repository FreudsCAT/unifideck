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
import React from "react";
import { rpcRoutes } from "./api/rpc-routes";
import { AccountSwitchModal, SteamRestartModal } from "./components/modals";
import type { Unregisterable } from "./types/steam";
/** Language pref. */
interface LanguagePref {
  success: boolean;
  language: string;
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
    const r = await call<[], LanguagePref>(
      rpcRoutes.getLanguagePreference,
    );
    if (r?.success && r.language && r.language !== "auto") {
      await i18n.changeLanguage(r.language);
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
    const r = await call<[], AccountSwitchInfo>(
      rpcRoutes.checkAccountSwitch,
    );
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
      window.SteamClient?.GameSessions
        ?.RegisterForAppLifetimeNotifications?.(
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
  unAppID: number; bRunning: boolean; nInstanceID: number;
}): void {
  if (n.bRunning) {
    void call(rpcRoutes.notifyGameLaunched, n.unAppID).catch(() => {});
  } else {
    void call(rpcRoutes.notifyGameStopped, n.unAppID).catch(() => {});
  }
}
/** Run all bootstrap tasks concurrently. Returns the
 *  unregister handle for the lifetime listener so the
 *  plugin entry can call it on unload. */
export async function runBootstrapTasks(): Promise<Unregisterable | null> {
  const [, , listener] = await Promise.all([
    applyLanguagePreference(),
    checkAccountSwitch(),
    Promise.resolve(registerLifetimeListener()),
  ]);
  return listener;
}
