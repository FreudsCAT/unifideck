/**
 * Game-size cache invalidation — the one place that decides when a measured
 * install size is stale.
 *
 * Two independent caches hold sizes, and they legitimately hold different
 * things: `useGameSize` keeps a number per `(appId, installed)` for a React
 * subtree, while `overview-enrichment` keeps one per appId and replays it into
 * `AppOverview.size_on_disk` from the `m_mapApps.set` patch. What they need to
 * share is not storage — it is *when to forget*.
 *
 * Before this, neither ever forgot. `useGameSize` relied on its cache key
 * changing when a game flipped from not-installed to installed, and
 * `overview-enrichment` measured each app at most once per Steam session. That
 * worked until a store reported "installed" before the game was actually on
 * disk: a wrapper store (Ubisoft, Battle.net) flipped the flag when its Wine
 * prefix was created, so the prefix-only measurement was written under the very
 * key the finished install would later read. The size then stayed wrong for the
 * whole life of the Steam JS context — restarting Steam was the only fix, which
 * is exactly how it was reported.
 *
 * The install-state flip is no longer premature, but relying on it as the sole
 * invalidation is still wrong: a game grows when it updates, shrinks when DLC
 * is removed, and nothing about either changes `installed`. So sizes are
 * forgotten explicitly, on the events that already tell us a game's bytes moved.
 *
 * Deliberately dependency-free — no `@decky/api`, no event-bus client — so any
 * module can import it without inheriting an import-order constraint.
 */

/** Drops every cached size for one app from a particular cache. */
type Clearer = (appId: number) => void;

const clearers = new Set<Clearer>();
const listeners = new Set<(appId: number) => void>();

/**
 * Register a cache's forget-this-app hook. Returns a disposer.
 *
 * A cache registers rather than exposing its map, so each stays free to key
 * itself however it needs to — `useGameSize` has to drop both the
 * installed and not-installed entries for an app, and its in-flight promises
 * with them.
 */
export function registerGameSizeCache(clear: Clearer): () => void {
  clearers.add(clear);
  return () => {
    clearers.delete(clear);
  };
}

/**
 * Forget every cached size for `appId`, then tell mounted consumers so they
 * refetch. Safe to call for an app nothing has measured.
 */
export function invalidateGameSize(appId: number): void {
  if (typeof appId !== "number" || !Number.isFinite(appId)) return;
  for (const clear of clearers) {
    try {
      clear(appId);
    } catch {
      /* one broken cache must not stop the others forgetting */
    }
  }
  for (const listener of listeners) {
    try {
      listener(appId);
    } catch {
      /* nor stop the rest being told */
    }
  }
}

/** Subscribe to invalidations. Returns a disposer. */
export function onGameSizeInvalidated(
  callback: (appId: number) => void,
): () => void {
  listeners.add(callback);
  return () => {
    listeners.delete(callback);
  };
}
