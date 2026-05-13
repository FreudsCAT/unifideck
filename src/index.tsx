/**
 * Plugin entry — the thin lifecycle wiring.
 *
 * Replaces the 2409-line legacy index.tsx with ~110 LOC
 * that does exactly what a Decky plugin entry should do :
 *
 *   1. Mount <RootProvider> around <QuickAccessPanel>
 *   2. Register the App Details router patch
 *   3. Run bootstrap tasks (language, account switch,
 *      lifetime listener)
 *   4. Return a teardown function for plugin unload
 *
 * That's it. Every other concern lives in F1-F5 :
 *  - SteamBridge isolates Steam internals
 *  - Contexts hold all reactive state
 *  - Hooks expose business actions
 *  - Components are pure presentational
 *  - Services drive the auth flows
 *
 * If this file grows past 200 LOC, something is being
 * smuggled in that should live in another layer. The size
 * is the safety net.
 */
import { definePlugin } from "@decky/api";
import { FaGamepad } from "react-icons/fa";
import { FC } from "react";
import { initI18n } from "./i18n";
import { SteamBridge } from "./lib/steam-bridge";
import { RootProvider } from "./contexts/RootProvider";
import { QuickAccessPanel } from "./views/QuickAccessPanel";
import { applyAppDetailsPatch } from "./views/AppDetailsPatch";
import { runBootstrapTasks } from "./bootstrap-tasks";
import { runTeardown, type TeardownHandles } from "./teardown";
// Eager translation load — Decky's UI mounts before any
// async work resolves, so we kick this off at module import.
void initI18n();
/** Panel content. */
const PanelContent: FC = () => (
  <RootProvider>
    <QuickAccessPanel />
  </RootProvider>
);
export default definePlugin(() => {
  console.log("[Unifideck] Plugin loaded");
  const bridge = new SteamBridge();
  const handles: TeardownHandles = {};
  // Apply the App Details patch immediately so the very
  // first navigation to a game's page shows our overrides.
  try {
    handles.routerPatch = applyAppDetailsPatch(bridge);
  } catch (e) {
    console.error("[Unifideck] router patch failed:", e);
  }
  // Bootstrap tasks (language, account switch, lifetime
  // listener) run async ; the lifetime listener handle is
  // captured for teardown when its promise resolves.
  void runBootstrapTasks().then((listener) => {
    handles.lifetimeListener = listener;
  });
  return {
    name: "Unifideck",
    titleView: <div>Unifideck</div>,
    content: <PanelContent />,
    icon: <FaGamepad />,
    onDismount: () => {
      console.log("[Unifideck] Plugin unloading");
      runTeardown(handles);
    },
  };
});
