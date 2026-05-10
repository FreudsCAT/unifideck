/**
 * Utils subpackage — barrel export.
 *
 * Runtime utilities shared across the frontend. Four pieces :
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
