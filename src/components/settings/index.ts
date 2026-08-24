/**
 * Settings — barrel export.
 *
 * The nine panels rendered in the QuickAccess Settings tab, in display
 * order : StoreConnections, LibrarySync, LanguageSelector,
 * GameDetailsViewModeToggle, CollectionsToggle, PluginUpdater,
 * CleanupSection, CaptureLogsSection, ManualInstallSection. Plus
 * StoreAuthButton, which is not a panel — it is the per-store button
 * used inside StoreConnections.
 *
 * All are presentational and reach into hooks/contexts for state.
 */
export { StoreConnections } from "./StoreConnections";
export { LibrarySync } from "./LibrarySync";
export { LanguageSelector } from "./LanguageSelector";
export { GameDetailsViewModeToggle } from "./GameDetailsViewModeToggle";
export { CollectionsToggle } from "./CollectionsToggle";
export { CleanupSection } from "./CleanupSection";
export { CaptureLogsSection } from "./CaptureLogsSection";
export { ManualInstallSection } from "./ManualInstallSection";
export { StoreAuthButton } from "./StoreAuthButton";
export { PluginUpdater } from "./PluginUpdater";
