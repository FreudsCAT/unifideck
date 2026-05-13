/**
 * Shared atoms — barrel export.
 *
 * Small, reusable pieces used by multiple feature areas :
 *  - StoreIcon : brand badge for Epic / GOG / Amazon / etc.
 *  - GameGrid  : reusable grid layout for any game list.
 *
 * Consumers should prefer these over rolling their own
 * markup so a brand-color change or a layout tweak is a
 * one-file edit.
 */
export { StoreIcon } from "./StoreIcon";
export { GameGrid } from "./GameGrid";
