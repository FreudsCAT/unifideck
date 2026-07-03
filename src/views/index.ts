/**
 * Views — barrel export.
 *
 * Top-level mounted UIs : `QuickAccessPanel` (the Decky
 * tab content) and `AppDetailsPatch` (the React-tree patch
 * applied to Steam's app details page).
 *
 * Views differ from components in that they orchestrate :
 * they mount multiple components, hooks and contexts to
 * deliver a complete user-facing screen. Components are
 * always pure presentational pieces.
 */
export { QuickAccessPanel } from "./QuickAccessPanel";
export { AppDetailsPatch, applyAppDetailsPatch } from "./AppDetailsPatch";
