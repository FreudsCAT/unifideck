/**
 * Settings — barrel export.
 *
 * Five settings panels exposed in the QuickAccess panel :
 * StorageSettings (decomposed from the 623L legacy file
 * into container + path picker), StoreConnections,
 * LibrarySync, LanguageSelector. All five are pure
 * presentational and reach into hooks/contexts for state.
 */
export { StoreConnections } from "./StoreConnections";
export { LibrarySync } from "./LibrarySync";
export { LanguageSelector } from "./LanguageSelector";
export { GameDetailsViewModeToggle } from "./GameDetailsViewModeToggle";
export { CleanupSection } from "./CleanupSection";
export { StoreAuthButton } from "./StoreAuthButton";
export { PluginUpdater } from "./PluginUpdater";
