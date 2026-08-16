/**
 * Capture Steam's Force-Compatibility choice into Unifideck's own pin,
 * then clear it in Steam before launching.
 *
 * ## Why this has to exist
 *
 * A Unifideck shortcut's `Exe` is `bin/unifideck-launcher` — a native Linux
 * script. When Steam has a Force-Compatibility **Proton** set on that
 * shortcut, `RunGame` does not merely put the launcher inside the Steam
 * Linux Runtime: it runs it *through Wine*. Captured from a device's own
 * `compat_log.txt`:
 *
 * ```
 * Command prefix for tool 2805730 "Proton 9.0-4" set to:
 *   SteamLinuxRuntime_sniper/_v2-entry-point --verb=waitforexitandrun --
 *   'Proton 9.0 (Beta)'/proton waitforexitandrun
 * ```
 *
 * Wine cannot execute a Linux shell script, so the launcher never starts —
 * no log, no toast, no game. Reproduced by hand: Wine boots, then exits
 * silently. Steam reported two such sessions lasting 14s and 5s with no
 * launcher log written at all.
 *
 * Nothing inside the launcher can fix that, because nothing inside the
 * launcher runs. The choice has to be moved out of Steam's hands *before*
 * `RunGame`, which is exactly what this module does:
 *
 *   1. read the Force-Compat tool Steam currently has for the shortcut;
 *   2. persist it as Unifideck's per-game pin (`proton_settings.json`);
 *   3. clear it in Steam so `RunGame` executes the launcher natively.
 *
 * The launcher then applies the pin itself — `selector.select_proton_version`
 * reads it as tier 2 — so the user's choice is honoured *and* the launcher
 * actually runs.
 *
 * ## Why the pin is refreshed at Play, not on page open
 *
 * An earlier version of this dance lived in a `useLaunchPrep` hook that ran
 * when the game-details page opened. Because the capture and the launch were
 * separated in time, the saved copy went stale whenever the user changed
 * Proton without revisiting that page — switching Proton "worked for some
 * games and not others purely by timing". Capturing on the Play press closes
 * that gap: the pin can never be older than the launch it applies to.
 *
 * ## What is deliberately left alone
 *
 * * **Steam Linux Runtime entries** (`is_linux_runtime`). Forcing SLR does
 *   *not* wrap the launcher in Wine — it runs natively inside the container,
 *   which `infrastructure/container_escape.py` already handles. It is also
 *   not a Proton, so pinning it would be meaningless.
 * * **Steam's global default** (`is_global_default`). A tool that matches
 *   `CompatToolMapping["0"]` is a distro-wide setting (Bazzite ships one),
 *   not a per-game choice by this user; adopting it would pin something they
 *   never picked onto every game they launch.
 *
 * Clearing is deliberately NOT undone after launch. Restoring it would make
 * the shortcut broken again for anyone starting the game from Steam's own
 * library entry rather than Unifideck's Play button — the pin, unlike Steam's
 * setting, works from both.
 */
import { call } from "@decky/api";

import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import type { ShortcutLaunchContext } from "../lib/steam-bridge/shortcut-types";

/** What {@link captureForceCompatPin} did, so the caller can tell the user. */
export type ProtonPinOutcome = {
  /** The tool now pinned for this game, or `null` if nothing was captured. */
  pinned: string | null;
  /** Why nothing was captured — for logging, not for display. */
  reason?: "none-set" | "linux-runtime" | "global-default" | "failed";
};

/**
 * Move Steam's Force-Compat choice for `storeGameId` into Unifideck's pin.
 *
 * `storeGameId` is the `"<store>:<game_id>"` key — the same string the
 * shortcut carries as the first token of its `LaunchOptions`, which is how
 * the backend resolves the appid, and the same key the launcher reads back.
 *
 * Never throws: a failure here must not stop the game from launching. The
 * worst case is the pre-existing behaviour (Steam keeps the setting, the
 * launcher may not start), which is no worse than not trying.
 */
export async function captureForceCompatPin(
  storeGameId: string,
): Promise<ProtonPinOutcome> {
  try {
    const raw = await call<[string], unknown>(
      rpcRoutes.getCompatToolForGame,
      storeGameId,
    );
    const ctx = unwrapRpcEnvelope<ShortcutLaunchContext>(raw, {
      route: rpcRoutes.getCompatToolForGame,
      throwing: false,
    });

    const tool = ctx?.tool_name ?? "";
    if (!tool) return { pinned: null, reason: "none-set" };
    if (ctx?.is_linux_runtime) return { pinned: null, reason: "linux-runtime" };
    if (ctx?.is_global_default) {
      return { pinned: null, reason: "global-default" };
    }

    await call<[string, string], unknown>(
      rpcRoutes.saveProtonSetting,
      storeGameId,
      tool,
    );

    // Only clear once the pin is safely stored. Clearing first and failing to
    // save would silently drop the user's choice on the floor.
    const appId = ctx?.appid_unsigned;
    if (appId) {
      window.SteamClient?.Apps?.SpecifyCompatTool?.(appId, "");
    }
    console.log(
      `[ProtonPin] pinned ${tool} for ${storeGameId} and cleared ` +
        `Steam Force-Compat (appid=${String(appId)})`,
    );
    return { pinned: tool };
  } catch (error) {
    console.error(`[ProtonPin] capture failed for ${storeGameId}:`, error);
    return { pinned: null, reason: "failed" };
  }
}
