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
 * The "Later" protection: pressing Later keeps the record pending, and
 * the launcher re-runs the installer on every Play. Temp-shortcut runs
 * are covered by the direct `watchAppStopped` hook; Play-initiated runs
 * of the persistent shortcut arrive as backend `game_stopped` events
 * (the lifetime listener → `notify_game_stopped` bridge). BOTH funnel
 * into `maybePromptForExe`, and a per-game guard stops the two signals
 * from stacking duplicate modals.
 *
 * Started from `definePlugin` (like `boot-event-listener`) so it works
 * with the Quick Access panel closed; teardown returns the unsubscribe.
 */
import { call, toaster } from "@decky/api";
import { showModal } from "@decky/ui";
import {
  ensureManualShortcut,
  offerRestartIfTileMissing,
} from "../lib/manual-restart";
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

/** How long after the launched app stops before re-writing the
 *  persistent shortcut. The temp shortcut's cleanup RemoveShortcut
 *  makes Steam flush its copy of shortcuts.vdf (which never contained
 *  our row) — the delay lets that flush land first so ours wins. */
const PERSIST_DELAY_MS = 2500;

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

/** Games with an exe-selection modal currently on screen — stops the
 *  temp-shortcut watch and the game_stopped event from double-prompting
 *  for the same run. */
const promptGuard = new Set<string>();

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

/** Show the exe-selection modal if the game is still pending. */
async function maybePromptForExe(gameId: string): Promise<void> {
  if (promptGuard.has(gameId)) return;
  const status = await fetchStatus(gameId);
  if (!status?.exists || status.status !== "installing") return;
  if (promptGuard.has(gameId)) return; // re-check after the await
  promptGuard.add(gameId);
  showModal(
    <ManualInstallExeModal
      gameId={gameId}
      gameTitle={status.title ?? gameId}
      installPath={status.install_path ?? "/"}
      onClosed={() => promptGuard.delete(gameId)}
    />,
  );
}

async function launchInstaller(storeGameId: string): Promise<void> {
  const gameId = storeGameId.split(":")[1] ?? storeGameId;
  const status = await fetchStatus(gameId);
  if (!status?.exists) {
    console.log(`${LOG_TAG} ${storeGameId} unknown — skipping launch`);
    return;
  }
  // "installing" → this run is the INSTALLER; "ready" → an IMPORTed
  // game's verification run (the prefix gets created now, and the user
  // sees the game actually launches — later runs are instant).
  const pending = status.status === "installing";
  const result = await launchWrapperViaShortcut(
    MANUAL_SHORTCUT_CONFIG,
    storeGameId,
  );
  if (!result.success || !result.app_id) {
    console.error(`${LOG_TAG} launch failed:`, result.error);
    toast("manualInstall.launchFailed", status.title ?? gameId);
    return;
  }
  if (pending) toast("manualInstall.installerRunning", status.title ?? gameId);
  // Temp-shortcut runs never surface as backend game_stopped events
  // (their appid resolves to no unifideck shortcut), so watch directly.
  const unsub = watchAppStopped(result.app_id, () => {
    unsub();
    if (pending) void maybePromptForExe(gameId);
    // Steam erased our persistent row when the temp shortcut was
    // added/removed (it flushes its own copy of shortcuts.vdf) — wait
    // out the cleanup's flush, then re-write the row so the tile
    // survives the next restart.
    window.setTimeout(() => {
      void ensureManualShortcut(gameId).finally(() => {
        // Verification run over (imported game) — the persistent tile
        // still needs Steam to re-read shortcuts.vdf.
        if (!pending) offerRestartIfTileMissing(gameId);
      });
    }, PERSIST_DELAY_MS);
  });
}

/** Launch a manual game through the temp-shortcut dance — used by the
 *  Downloads row when the persistent shortcut is not registered in this
 *  Steam session yet (a plain RunGame would fail with Steam's "Game
 *  configuration unavailable"). */
export function launchManualGame(gameId: string): void {
  void launchInstaller(`manual:${gameId}`);
}

/**
 * Subscribe to the manual-install launch requests and to manual game
 * exits (the "Later" re-prompt). Returns the combined unsubscribe.
 */
export function startManualInstallListener(): () => void {
  const unsubs: Array<() => void> = [];
  unsubs.push(
    EventBusClient.subscribe(
      Events.MANUAL_INSTALL_LAUNCH_REQUESTED,
      (payload) => {
        const storeGameId = payload.store_game_id;
        if (typeof storeGameId !== "string" || !storeGameId) return;
        void launchInstaller(storeGameId);
      },
    ),
  );
  // Any run of a manual game that ends while the record is still
  // "installing" re-offers the exe selection — this is what makes
  // "Later" safe: Play → installer runs again → prompt comes back,
  // regardless of where the launch started.
  unsubs.push(
    EventBusClient.subscribe(Events.GAME_STOPPED, (payload) => {
      if (payload.store !== "manual") return;
      const gameId = payload.game_id;
      if (typeof gameId !== "string" || !gameId) return;
      void maybePromptForExe(gameId);
    }),
  );
  return () => {
    for (const unsub of unsubs) unsub();
  };
}
