/**
 * CollectionManager — auto-generates `[Unifideck] *` Steam Collections
 * mirroring the UNIFIDECK_TABS list.
 *
 * Ported from `staging:src/spoofing/CollectionManager.ts`. Wires into
 * Steam's `window.collectionStore` and `window.appStore` — the only
 * two Steam globals not abstracted by `SteamBridge` because the
 * collection APIs are too coupled to Steam's React tree to project.
 */
import { getUnifideckTabs, type UnifideckTab } from "./tab-container";
import { runFilters } from "../library-filters";
import type { SteamAppOverview } from "../../types/steam";

const COLLECTION_PREFIX = "[Unifideck] ";
// Non-Steam shortcuts (ours + the user's) carry this app_type; exclude
// them so we never feed Ubisoft titles back into the Steam-owned filter.
const NON_STEAM_SHORTCUT_APP_TYPE = 1073741824;

/**
 * Set by `deleteAllUnifideckCollections` so the initial-load sync in
 * `startCollectionManager` doesn't immediately recreate the collections
 * the user just wiped. Cleared on the next `unifideck-sync-completed` —
 * a fresh library sync is the explicit signal collections are wanted
 * again. Lives in localStorage so it survives the Steam restart that
 * cleanup prompts for.
 */
const SUPPRESSION_KEY = "unifideck.collections.suppressed";

function isSuppressed(): boolean {
  try {
    return window.localStorage.getItem(SUPPRESSION_KEY) === "1";
  } catch {
    return false;
  }
}

function setSuppressed(on: boolean): void {
  try {
    if (on) window.localStorage.setItem(SUPPRESSION_KEY, "1");
    else window.localStorage.removeItem(SUPPRESSION_KEY);
  } catch {
    /* localStorage unavailable — worst case is pre-fix behavior */
  }
}

interface AppStoreOverview {
  appid: number;
  display_name: string;
  app_type?: number;
  installed?: boolean;
  steam_deck_compat_category?: number;
}

interface Collection {
  AsDragDropCollection: () => {
    AddApps: (overviews: AppStoreOverview[]) => void;
    RemoveApps: (overviews: AppStoreOverview[]) => void;
  };
  Save: () => Promise<void>;
  Delete: () => Promise<void>;
  allApps: AppStoreOverview[];
  displayName: string;
  id: string;
}

interface CollectionStore {
  GetCollection: (id: string) => Collection | undefined;
  GetCollectionIDByUserTag: (tag: string) => string | null;
  NewUnsavedCollection: (
    tag: string,
    filter: unknown,
    overviews: AppStoreOverview[],
  ) => Collection | undefined;
  userCollections: Map<string, Collection>;
}

interface AppStore {
  GetAppOverviewByAppID: (appId: number) => AppStoreOverview | null;
}

function getCollectionStore(): CollectionStore | null {
  return (
    (window as unknown as { collectionStore?: CollectionStore })
      .collectionStore ?? null
  );
}

function getAppStore(): AppStore | null {
  return (window as unknown as { appStore?: AppStore }).appStore ?? null;
}

function tabName(tab: UnifideckTab): string {
  return `${COLLECTION_PREFIX}${tab.title}`;
}

function validCollectionNames(): Set<string> {
  return new Set(getUnifideckTabs().map(tabName));
}

async function deleteCollection(c: Collection): Promise<void> {
  try {
    await c.Delete();
  } catch (e) {
    console.error(`[Unifideck Collections] delete ${c.displayName} failed`, e);
  }
}

/**
 * Snapshot of every `[Unifideck]` collection. `Delete()` mutates the
 * underlying MobX Map, so iterating `userCollections.values()` live
 * while deleting skips entries — always work from a snapshot.
 */
function snapshotUnifideckCollections(cs: CollectionStore): Collection[] {
  let collections: Map<string, Collection> | undefined;
  try {
    collections = cs.userCollections;
  } catch {
    return [];
  }
  if (!collections || typeof collections.values !== "function") return [];
  return Array.from(collections.values()).filter((c) =>
    c?.displayName?.startsWith(COLLECTION_PREFIX),
  );
}

async function cleanupStaleCollections(): Promise<void> {
  const cs = getCollectionStore();
  if (!cs) return;
  const valid = validCollectionNames();
  for (const c of snapshotUnifideckCollections(cs)) {
    if (!valid.has(c.displayName)) {
      await deleteCollection(c);
    }
  }
}

async function getOrCreateCollection(tag: string): Promise<Collection | null> {
  const cs = getCollectionStore();
  if (!cs) return null;
  const id = cs.GetCollectionIDByUserTag(tag);
  if (typeof id === "string") {
    const existing = cs.GetCollection(id);
    if (existing) return existing;
  }
  const created = cs.NewUnsavedCollection(tag, undefined, []);
  if (!created) return null;
  await created.Save();
  return created;
}

async function clearCollection(c: Collection): Promise<void> {
  const apps = c.allApps ?? [];
  if (apps.length === 0) return;
  c.AsDragDropCollection().RemoveApps(apps);
  await c.Save();
}

async function syncTab(
  tab: UnifideckTab,
  allApps: SteamAppOverview[],
): Promise<boolean> {
  const matches = allApps.filter(
    (a) => a.appid > 0 && runFilters(tab.filters, a),
  );
  if (matches.length === 0) {
    // Nothing to show — don't create an empty `[Unifideck]` shell, and
    // drop any leftover one from a previous sync.
    const cs = getCollectionStore();
    if (!cs) return false;
    const id = cs.GetCollectionIDByUserTag(tabName(tab));
    if (typeof id === "string") {
      const existing = cs.GetCollection(id);
      if (existing) await deleteCollection(existing);
    }
    return true;
  }
  const c = await getOrCreateCollection(tabName(tab));
  if (!c) return false;
  const appStore = getAppStore();
  if (!appStore) return false;
  await clearCollection(c);
  const overviews: AppStoreOverview[] = [];
  for (const a of matches) {
    try {
      const o = appStore.GetAppOverviewByAppID(a.appid);
      if (o) overviews.push(o);
    } catch {
      /* skip */
    }
  }
  if (overviews.length > 0) {
    c.AsDragDropCollection().AddApps(overviews);
    await c.Save();
  }
  return true;
}

/** Sync every `[Unifideck]` collection to current tab filters. */
export async function syncUnifideckCollections(): Promise<void> {
  if (isSuppressed()) return;
  if (!isCollectionsAvailable()) return;
  await cleanupStaleCollections();
  const cs = getCollectionStore();
  if (!cs) return;
  let allApps: SteamAppOverview[] = [];
  try {
    const games = cs.GetCollection("type-games");
    allApps = (games?.allApps ?? []) as unknown as SteamAppOverview[];
  } catch {
    return;
  }
  if (allApps.length === 0) return;
  await Promise.allSettled(getUnifideckTabs().map((t) => syncTab(t, allApps)));
}

/**
 * Display names of every Steam game the user owns — installed or not —
 * from Steam's "type-games" collection. Non-Steam shortcuts are excluded
 * (see {@link NON_STEAM_SHORTCUT_APP_TYPE}). `appmanifest` only knows
 * installed games, so this is the only way the backend learns about
 * owned-but-not-installed Steam games.
 */
export function collectSteamOwnedGameTitles(): string[] {
  const cs = getCollectionStore();
  if (!cs) return [];
  let allApps: SteamAppOverview[] = [];
  try {
    const games = cs.GetCollection("type-games");
    allApps = (games?.allApps ?? []) as unknown as SteamAppOverview[];
  } catch {
    return [];
  }
  const titles = new Set<string>();
  for (const a of allApps) {
    if (!a || a.appid <= 0 || a.app_type === NON_STEAM_SHORTCUT_APP_TYPE) {
      continue;
    }
    const name = a.display_name?.trim();
    if (name) titles.add(name);
  }
  return Array.from(titles);
}

/**
 * Delete every `[Unifideck]` collection and suppress re-creation until
 * the next library sync. Verifies the store actually dropped them
 * (deletes persist asynchronously and can race a Steam restart),
 * retrying leftovers a few times before giving up.
 */
export async function deleteAllUnifideckCollections(): Promise<void> {
  setSuppressed(true);
  const cs = getCollectionStore();
  if (!cs) return;
  // Tag-based pass first — deterministic lookup for the current locale;
  // the prefix scan below also catches collections created under a
  // different UI language.
  for (const tab of getUnifideckTabs()) {
    try {
      const id = cs.GetCollectionIDByUserTag(tabName(tab));
      if (typeof id === "string") {
        const c = cs.GetCollection(id);
        if (c) await deleteCollection(c);
      }
    } catch {
      /* skip */
    }
  }
  for (let attempt = 0; attempt < 3; attempt++) {
    const targets = snapshotUnifideckCollections(cs);
    if (targets.length === 0) return;
    for (const c of targets) await deleteCollection(c);
    await new Promise((r) => setTimeout(r, 250));
  }
  const survivors = snapshotUnifideckCollections(cs);
  if (survivors.length > 0) {
    console.error(
      "[Unifideck Collections] collections survived deletion:",
      survivors.map((c) => c.displayName),
    );
  }
}

export function isCollectionsAvailable(): boolean {
  const s = getCollectionStore();
  if (
    !s ||
    typeof s.GetCollectionIDByUserTag !== "function" ||
    typeof s.NewUnsavedCollection !== "function"
  )
    return false;
  try {
    // ``GetCollection("type-games")`` is a synchronous lookup
    // by tag; it accesses the underlying store directly without
    // going through the ``userCollections`` MobX-computed getter
    // that throws when the store is half-hydrated. Once
    // ``type-games`` is resolvable with a real ``allApps`` array,
    // the whole collection graph is safe to traverse.
    const games = s.GetCollection("type-games");
    return Boolean(games && Array.isArray(games.allApps));
  } catch {
    return false;
  }
}

/** Manager handle returned by `startCollectionManager`. */
export interface CollectionManagerHandle {
  resync(): Promise<void>;
  remove(): void;
}

async function waitForCollections(timeoutMs = 30_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (isCollectionsAvailable()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

/**
 * Subscribes to `unifideck-sync-completed` so collections are rebuilt
 * after every library sync. Returns a handle whose `remove()` detaches
 * the listener — call it from plugin teardown.
 */
export function startCollectionManager(): CollectionManagerHandle {
  const onSync = () => {
    // A fresh library sync means the user wants collections back —
    // lift any post-cleanup suppression before rebuilding.
    setSuppressed(false);
    void syncUnifideckCollections().catch((e) =>
      console.error("[Unifideck Collections] resync failed", e),
    );
  };
  window.addEventListener("unifideck-sync-completed", onSync);
  void waitForCollections()
    .then((ready) => {
      if (!ready) {
        console.warn(
          "[Unifideck Collections] store never became ready — skipping initial sync",
        );
        return;
      }
      return syncUnifideckCollections();
    })
    .catch((e) =>
      console.error("[Unifideck Collections] initial sync failed", e),
    );
  return {
    resync: syncUnifideckCollections,
    remove: () =>
      window.removeEventListener("unifideck-sync-completed", onSync),
  };
}
