/**
 * manual-install-listener — RunGame bridge for the Manual Install flow.
 *
 * The backend cannot open the user's installer itself: in Gaming Mode a
 * bare subprocess has no gamescope session and its window never appears
 * (the exact constraint the wrapper stores solve). So after
 * `manual_install_start` registers the game — with its games.map row
 * pointing at the installer — the backend emits
 * `MANUAL_INSTALL_LAUNCH_REQUESTED` and this listener:
 *
 *  1. RunGames the game's shortcut (temporary AddShortcut stand-in when
 *     the freshly written persistent one isn't in Steam's app store
 *     yet). The launcher then creates the Proton prefix, maps drive
 *     `D:` to the install folder, and runs the installer under umu.
 *  2. Watches for the app to stop (the installer exited) and, while the
 *     record is still pending, asks the user for the game's executable
 *     via `ManualInstallExeModal`.
 *
 * Started from `definePlugin` (like `boot-event-listener`) so it works
 * with the Quick Access panel closed; teardown returns the unsubscribe.
 */
import { call, toaster } from "@decky/api";
import { showModal } from "@decky/ui";
import i18n from "i18next";
import { EventBusClient } from "../api/event-bus-client";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { Events } from "../types/events";
import {
  launchWrapperViaShortcut,
  type WrapperShortcutConfig,
} from "../lib/steam-bridge/wrapper-shortcut-launch";
import { watchAppStopped } from "../lib/steam-bridge/shortcut-types";
import { ManualInstallExeModal } from "../components/modals/ManualInstallExeModal";

const LOG_TAG = "[ManualInstallListener]";

/** Only the launch-path fields matter here — the auth-flow fields are
 *  required by the type but never exercised for the manual store. */
const MANUAL_SHORTCUT_CONFIG: WrapperShortcutConfig = {
  storeId: "manual",
  displayName: "Unifideck Manual Install",
  logTag: LOG_TAG,
  authShortcutStoreId: "manual:no-auth",
  authContextRoute: "",
  actionEnvVar: "UNIFIDECK_MANUAL_ACTION",
};

interface ManualStatus {
  exists?: boolean;
  status?: string;
  title?: string;
  install_path?: string;
}

function toast(titleKey: string, body: string): void {
  try {
    toaster.toast({ title: String(i18n.t(titleKey)), body, duration: 5000 });
  } catch {
    console.log(`${LOG_TAG} ${titleKey}: ${body}`);
  }
}

async function fetchStatus(gameId: string): Promise<ManualStatus | null> {
  try {
    const raw = await call<[string], unknown>(
      rpcRoutes.manualInstallStatus,
      gameId,
    );
    return unwrapRpcEnvelope<ManualStatus>(raw, {
      route: rpcRoutes.manualInstallStatus,
      throwing: false,
    });
  } catch (e) {
    console.error(`${LOG_TAG} status fetch failed for ${gameId}:`, e);
    return null;
  }
}

/** Once the installer app stops, ask for the exe if still pending. */
function armStopWatch(appId: number, gameId: string): void {
  const unsub = watchAppStopped(appId, () => {
    unsub();
    void (async () => {
      const status = await fetchStatus(gameId);
      if (!status?.exists || status.status !== "installing") return;
      showModal(
        <ManualInstallExeModal
          gameId={gameId}
          gameTitle={status.title ?? gameId}
          installPath={status.install_path ?? "/"}
        />,
      );
    })();
  });
}

async function launchInstaller(storeGameId: string): Promise<void> {
  const gameId = storeGameId.split(":")[1] ?? storeGameId;
  const status = await fetchStatus(gameId);
  if (!status?.exists || status.status !== "installing") {
    console.log(`${LOG_TAG} ${storeGameId} not pending — skipping launch`);
    return;
  }
  const result = await launchWrapperViaShortcut(
    MANUAL_SHORTCUT_CONFIG,
    storeGameId,
  );
  if (!result.success || !result.app_id) {
    console.error(`${LOG_TAG} installer launch failed:`, result.error);
    toast("manualInstall.launchFailed", status.title ?? gameId);
    return;
  }
  toast("manualInstall.installerRunning", status.title ?? gameId);
  armStopWatch(result.app_id, gameId);
}

/**
 * Subscribe to the manual-install launch requests. Returns the
 * unsubscribe function for teardown.
 */
export function startManualInstallListener(): () => void {
  return EventBusClient.subscribe(
    Events.MANUAL_INSTALL_LAUNCH_REQUESTED,
    (payload) => {
      const storeGameId = payload.store_game_id;
      if (typeof storeGameId !== "string" || !storeGameId) return;
      void launchInstaller(storeGameId);
    },
  );
}
