/**
 * shortcut-ownership — "is this shortcut ours?", answered synchronously.
 *
 * `isUnifideckGame` (library-filters) is the authoritative answer, but it is
 * backed by an RPC-loaded cache, so it has nothing to say until that cache
 * lands — and it retries with backoff for up to five attempts on failure.
 * `AppDetailsPatch` used to stay *optimistic* through that whole window,
 * treating every non-Steam shortcut (`appid > 2e9`) as ours: marking the
 * container, hiding Steam's native Play row and the entire lower tabbed
 * section, and injecting our own UI. On a device with 719 shortcuts, 27 of
 * which belong to EmulationStationDE, EmuDeck and friends, that meant
 * blanking a neighbour's App-Details page for as long as the cache took.
 *
 * The optimism existed for a real reason — waiting for the cache made *our*
 * games flash Steam's native UI first — so the fix is not to drop it but to
 * add a second, synchronous signal that can say "definitely not ours" on its
 * own. Every Unifideck shortcut has the same launcher as its `Exe`, and Steam
 * has it in hand on the very page being rendered.
 *
 * Verified against the live client over CDP: `appDetailsStore.GetAppDetails`
 * returns `strShortcutExe` (quoted, e.g.
 * `"\"/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher\""`) plus
 * `strShortcutLaunchOptions` (`"epic:Sugar"`) — and returns `null` outright
 * for an app whose details are not loaded, which is why "unknown" has to be a
 * first-class answer rather than a `false`.
 */

/** Steam's app-details store, as much of it as we read. */
interface AppDetailsLike extends Record<string, unknown> {
  strShortcutExe?: unknown;
}

interface AppDetailsStoreLike {
  GetAppDetails: (id: number) => AppDetailsLike | null;
}

/** Basename of the launcher every Unifideck shortcut points at
 *  (`bin/unifideck-launcher`). Matched as a substring so a relocated
 *  `homebrew` root, a Flatpak Steam path, or the surrounding quotes Steam
 *  stores can't defeat it. */
const LAUNCHER_MARKER = "unifideck-launcher";

function getAppDetailsStore(): AppDetailsStoreLike | null {
  return (
    (window as unknown as { appDetailsStore?: AppDetailsStoreLike })
      .appDetailsStore ?? null
  );
}

/**
 * Whether `appId` is a Unifideck shortcut, from Steam's own shortcut data.
 *
 * Returns `true` / `false` when Steam has the shortcut's `Exe` in hand, and
 * `null` when it does not — no store, no details for this app yet, or no exe
 * recorded. `null` means "no opinion": callers must fall back to whatever
 * they did before rather than treating it as `false`, so a Steam-side rename
 * of the field degrades to the previous behaviour instead of hiding our UI on
 * our own games.
 */
export function isUnifideckShortcut(appId: number): boolean | null {
  const store = getAppDetailsStore();
  if (!store?.GetAppDetails) return null;
  let details: AppDetailsLike | null = null;
  try {
    details = store.GetAppDetails(appId);
  } catch {
    // Steam throws for ids it has never heard of; that is not evidence.
    return null;
  }
  const exe = details?.strShortcutExe;
  if (typeof exe !== "string" || !exe) return null;
  return exe.includes(LAUNCHER_MARKER);
}

/**
 * Whether the App-Details patch should treat `appId` as one of ours.
 *
 * Combines the two signals so that *either* saying "not ours" is enough to
 * bail, while optimism survives only while both are silent:
 *
 * | cache loaded | cache says ours | exe probe | result |
 * |---|---|---|---|
 * | no  | –     | ours    | patch (no flash on our games)   |
 * | no  | –     | foreign | **skip** — the bug this fixes   |
 * | no  | –     | unknown | patch (previous behaviour)      |
 * | yes | yes   | any     | patch                           |
 * | yes | no    | any     | skip                            |
 */
export function shouldPatchShortcut(
  appId: number,
  cacheLoaded: boolean,
  cacheSaysOurs: boolean,
): boolean {
  if (cacheLoaded) return cacheSaysOurs;
  return isUnifideckShortcut(appId) !== false;
}
