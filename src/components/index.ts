/**
 * Components — barrel export.
 *
 * Re-exports every presentational component from its sub-
 * package so consumers can write
 *
 *   import { GameInfoPanel, PlaySectionWrapper } from "../components";
 *
 * without knowing which sub-folder hosts which file. Each
 * sub-package (play / info / settings / modals / downloads /
 * shared) has its own barrel that this file aggregates.
 *
 * Hard rule : files in this barrel are PRESENTATIONAL ONLY.
 * Business logic lives in `hooks/` (Phase F3) or `services/`
 * (Phase F5). A component that calls `call()` or
 * `window.SteamClient` directly is a bug — it must go
 * through the appropriate hook or the SteamBridge.
 */
export * from "./play";
export * from "./info";
export * from "./settings";
export * from "./modals";
export * from "./downloads";
export * from "./shared";
