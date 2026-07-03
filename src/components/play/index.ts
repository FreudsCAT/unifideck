/**
 * Play section override — barrel export.
 *
 * Decomposes the legacy PlayButtonOverride.tsx (2479 LOC)
 * into a dispatcher (`PlaySectionWrapper`) and three
 * state-specific button groups (NotInstalled / Downloading
 * / Installed). The dispatcher reads `usePlaySection` and
 * routes to the correct presentation, eliminating the giant
 * if/else chain that polluted the legacy file.
 */
export { PlaySectionWrapper } from "./PlaySectionWrapper";
export { NotInstalledButtons } from "./NotInstalledButtons";
export { DownloadingButtons } from "./DownloadingButtons";
export { InstalledButtons } from "./InstalledButtons";
