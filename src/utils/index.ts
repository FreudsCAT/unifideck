/**
 * Utils subpackage — barrel export.
 *
 * Runtime utilities shared across the frontend. Five pieces :
 *
 *  - format.ts             : pure formatting helpers (bytes,
 *                            ETA) used by the downloads UI.
 *  - controllerConfig.ts   : Steam controller-launch helper
 *                            that goes through SteamBridge.
 *  - authShortcutLaunch.ts : generic auth-via-shortcut
 *                            launcher for Epic/GOG/Amazon/
 *                            Microsoft.
 *  - ubisoftShortcutLaunch : Ubisoft-specific install/auth
 *                            launcher (reuses existing shortcut
 *                            + restores proton tool).
 *  - clipboard.ts          : owning-window-aware clipboard
 *                            write (Gaming Mode renders the QAM
 *                            into a separate popup window).
 *
 * Any module that does runtime work outside React but inside
 * the frontend belongs here. Pure type-only modules belong in
 * `types/` ; React state or RPC wrappers belong in `hooks/` or
 * `contexts/`.
 */
export * from "./format";
export * from "./controllerConfig";
export * from "./authShortcutLaunch";
export * from "./ubisoftShortcutLaunch";
export * from "./clipboard";
