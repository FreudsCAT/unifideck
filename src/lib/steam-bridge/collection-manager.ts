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

async function cleanupStaleCollections(): Promise<void> {
  const cs = getCollectionStore();
  if (!cs) return;
  let collections: Map<string, Collection> | undefined;
  try {
    collections = cs.userCollections;
  } catch {
    return;
  }
  if (!collections || typeof collections.values !== "function") return;
  const valid = validCollectionNames();
  for (const c of collections.values()) {
    if (
      c?.displayName?.startsWith(COLLECTION_PREFIX) &&
      !valid.has(c.displayName)
    ) {
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

/** Delete every `[Unifideck]` collection. */
export async function deleteAllUnifideckCollections(): Promise<void> {
  const cs = getCollectionStore();
  if (!cs) return;
  let collections: Map<string, Collection> | undefined;
  try {
    collections = cs.userCollections;
  } catch {
    return;
  }
  if (!collections || typeof collections.values !== "function") return;
  for (const c of collections.values()) {
    if (c?.displayName?.startsWith(COLLECTION_PREFIX))
      await deleteCollection(c);
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
