/**
 * Per-appId version counter for AppDetails injection keys.
 *
 * Bumping the version for an appId re-keys the injected
 * `<PlaySectionWrapper>` and `<GameInfoPanel>` so React fully
 * re-mounts them on game-state changes (install / uninstall).
 *
 * Lives in `lib/` rather than `views/AppDetailsPatch.tsx` so
 * hooks (`useGameActions`) can bump the version without
 * importing `views/`, which would create a cycle :
 *   useGameActions → views/AppDetailsPatch
 *     → components/play
 *       → PlaySectionWrapper → NotInstalledButtons → useGameActions.
 */
const versions = new Map<number, number>();

/** Read the current version for an appId (0 by default). */
export function getGameStateVersion(appId: number): number {
  return versions.get(appId) ?? 0;
}

/** Increment the version so the next patch re-mounts the
 *  Unifideck overrides for this appId. Idempotent and cheap. */
export function bumpGameStateVersion(appId: number): void {
  versions.set(appId, (versions.get(appId) ?? 0) + 1);
}
