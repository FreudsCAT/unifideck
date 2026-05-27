/**
 * Library tab filters — pure predicates over `SteamAppOverview`.
 *
 * Powers the custom Unifideck library tabs. Each filter receives an
 * app overview and returns whether the app belongs in the tab. The
 * Unifideck game cache (store-of-record for non-Steam shortcuts we
 * manage) and the third-party shortcut allow-list are populated by
 * `LibraryContext` from `get_all_unifideck_games`.
 *
 * Ported from `staging:src/tabs/filters.ts`. The module-level caches
 * mirror staging's design — keeping them outside React lets the
 * Steam library-patch hook call `runFilters` from a non-component
 * context without prop-drilling.
 */
import { call } from "@decky/api";
import { unwrapRpcEnvelope } from "../../api/useRPC";
import { EventBusClient } from "../../api/event-bus-client";
import { Events } from "../../types/events";
import {
  getCachedCompatByTitle,
  getCachedRating,
  meetsGreatOnDeckCriteria,
} from "../protondb-cache";
import type { SteamAppOverview } from "../../types/steam";

export type StoreSlug =
  | "steam"
  | "epic"
  | "gog"
  | "amazon"
  | "ubisoft"
  | "microsoft";

export type FilterType =
  | "installed"
  | "platform"
  | "store"
  | "deckCompat"
  | "all"
  | "nonSteam";

export interface FilterParams {
  installed: { installed: boolean };
  platform: { platform: "steam" | "nonSteam" | "all" };
  store: { store: StoreSlug | "all" };
  deckCompat: Record<string, never>;
  all: Record<string, never>;
  nonSteam: Record<string, never>;
}

export interface TabFilter<T extends FilterType = FilterType> {
  type: T;
  params: FilterParams[T];
}

const NON_STEAM_APP_TYPE = 1073741824;
const DECK_VERIFIED = 3;

interface UnifideckCacheEntry {
  store: Exclude<StoreSlug, "steam">;
  isInstalled: boolean;
  steamAppId?: number;
}

/** Stored in both signed and unsigned forms — Steam returns the
 *  appid signed in some surfaces and unsigned in others, and the
 *  filter functions can't predict which. */
export const unifideckGameCache: Map<number, UnifideckCacheEntry> = new Map();

/** Version bumped on every per-app status change — patchers read
 *  this to know when to remount React subtrees. */
export const gameStateVersion: Map<number, number> = new Map();

/** AppIds of non-Unifideck shortcuts whose Exe path is valid.
 *  Anything else under `app_type === NON_STEAM_APP_TYPE` is a
 *  broken shortcut and excluded from "Installed". */
export const validThirdPartyCache: Set<number> = new Set();

type ForceRefreshCallback = (appId: number) => Promise<void>;
let forceRefreshCallback: ForceRefreshCallback | null = null;

export function setForceRefreshCallback(cb: ForceRefreshCallback | null): void {
  forceRefreshCallback = cb;
}

function variantIds(appId: number): number[] {
  const ids = new Set<number>([appId]);
  if (appId < 0) ids.add(appId + 0x100000000);
  if (appId >= 0 && appId > 0x7fffffff) ids.add(appId - 0x100000000);
  return [...ids];
}

export interface UnifideckGameInput {
  appId: number;
  store: Exclude<StoreSlug, "steam">;
  isInstalled: boolean;
  steamAppId?: number;
}

export function updateUnifideckCache(games: UnifideckGameInput[]): void {
  unifideckGameCache.clear();
  for (const g of games) {
    const entry: UnifideckCacheEntry = {
      store: g.store,
      isInstalled: g.isInstalled,
      steamAppId: g.steamAppId,
    };
    for (const id of variantIds(g.appId)) unifideckGameCache.set(id, entry);
  }
}

export function updateSingleGameStatus(g: UnifideckGameInput): void {
  const existing = unifideckGameCache.get(g.appId);
  const entry: UnifideckCacheEntry = {
    store: g.store,
    isInstalled: g.isInstalled,
    steamAppId: existing?.steamAppId ?? g.steamAppId,
  };
  for (const id of variantIds(g.appId)) unifideckGameCache.set(id, entry);
  if (
    existing &&
    existing.isInstalled === g.isInstalled &&
    existing.store === g.store
  )
    return;
  const next = (gameStateVersion.get(g.appId) ?? 0) + 1;
  for (const id of variantIds(g.appId)) gameStateVersion.set(id, next);
  if (forceRefreshCallback) {
    void forceRefreshCallback(g.appId).catch((e) =>
      console.error("[Unifideck] force-refresh callback failed", e),
    );
  }
  window.dispatchEvent(
    new CustomEvent("unifideck-game-state-changed", {
      detail: { appId: g.appId, isInstalled: g.isInstalled, store: g.store },
    }),
  );
}

export function updateValidThirdPartyCache(appIds: number[]): void {
  validThirdPartyCache.clear();
  for (const id of appIds) {
    for (const v of variantIds(id)) validThirdPartyCache.add(v);
  }
}

export function isUnifideckGame(appId: number): boolean {
  return unifideckGameCache.has(appId);
}

export function getStoreForApp(
  appId: number,
  appType: number,
): StoreSlug | null {
  const cached = unifideckGameCache.get(appId);
  if (cached) return cached.store;
  if (appType === NON_STEAM_APP_TYPE) return null;
  return "steam";
}

export function getInstalledStatus(
  appId: number,
  _appType: number,
  steamInstalledFlag: boolean,
): boolean {
  return unifideckGameCache.get(appId)?.isInstalled ?? steamInstalledFlag;
}

type FilterFn<K extends FilterType> = (
  params: FilterParams[K],
  app: SteamAppOverview,
) => boolean;

const filterFunctions: { [K in FilterType]: FilterFn<K> } = {
  all: (_p, app) => {
    if (app.app_type !== NON_STEAM_APP_TYPE) return true;
    return isUnifideckGame(app.appid);
  },
  installed: (params, app) => {
    const isInstalled = getInstalledStatus(
      app.appid,
      app.app_type,
      app.installed,
    );
    const match = params.installed ? isInstalled : !isInstalled;
    if (params.installed && app.app_type === NON_STEAM_APP_TYPE) {
      if (unifideckGameCache.has(app.appid)) return match;
      if (
        validThirdPartyCache.size > 0 &&
        !validThirdPartyCache.has(app.appid)
      ) {
        return false;
      }
    }
    return match;
  },
  platform: (params, app) => {
    if (params.platform === "all") return true;
    if (params.platform === "steam") return app.app_type !== NON_STEAM_APP_TYPE;
    return app.app_type === NON_STEAM_APP_TYPE;
  },
  store: (params, app) => {
    if (params.store === "all") return true;
    const store = getStoreForApp(app.appid, app.app_type);
    if (store === null) return false;
    return store === params.store;
  },
  deckCompat: (_p, app) => {
    if (app.steam_deck_compat_category === DECK_VERIFIED) return true;
    const cached = unifideckGameCache.get(app.appid);
    if (cached) {
      const title = app.display_name || "";
      if (!title) return false;
      return meetsGreatOnDeckCriteria(getCachedCompatByTitle(title));
    }
    const tier = getCachedRating(app.appid);
    return tier === "native" || tier === "platinum";
  },
  nonSteam: (_p, app) => {
    if (app.app_type !== NON_STEAM_APP_TYPE) return false;
    if (unifideckGameCache.has(app.appid)) return false;
    return true;
  },
};

function getHiddenAppIds(): Set<number> {
  try {
    const cs = (
      window as unknown as {
        collectionStore?: {
          GetCollection?: (
            id: string,
          ) => { allApps?: Array<{ appid: number }> } | null;
        };
      }
    ).collectionStore;
    const hidden = cs?.GetCollection?.("hidden");
    if (hidden?.allApps) return new Set(hidden.allApps.map((a) => a.appid));
  } catch (e) {
    console.error("[Unifideck] hidden collection lookup failed", e);
  }
  return new Set();
}

export function isGameHidden(appId: number): boolean {
  return getHiddenAppIds().has(appId);
}

export function runFilter<T extends FilterType>(
  filter: TabFilter<T>,
  app: SteamAppOverview,
): boolean {
  const fn = filterFunctions[filter.type] as FilterFn<T> | undefined;
  if (!fn) return true;
  return fn(filter.params, app);
}

export function runFilters(
  filters: TabFilter[],
  app: SteamAppOverview,
): boolean {
  if (isGameHidden(app.appid)) return false;
  return filters.every((f) => runFilter(f, app));
}

// Shape of one row from the ``get_all_unifideck_games`` RPC —
// matches Python's ``asdict(Game)``. The frontend ``Game``
// interface in ``types/api.ts`` is misaligned (it expects
// ``is_installed`` / ``executable``); ignore it and trust the
// actual serialised field names from the backend.
interface RpcGameRow {
  app_id?: number | null;
  store?: StoreSlug;
  store_game_id?: string;
  installed?: boolean;
  metadata?: Record<string, unknown>;
}

type StoreCounts = Partial<Record<Exclude<StoreSlug, "steam">, number>>;
type StoreCountSink = (counts: StoreCounts) => void;

let storeCountSink: StoreCountSink | null = null;
let cacheLoadStarted = false;

/** Register a callback to receive per-store game counts. The
 *  tab manager uses this to drive ``shouldShowTab``. */
export function setStoreCountSink(sink: StoreCountSink | null): void {
  storeCountSink = sink;
}

function isNonSteamStore(
  s: StoreSlug | undefined,
): s is Exclude<StoreSlug, "steam"> {
  return s !== undefined && s !== "steam";
}

/** Fetch the unified game list from the backend and populate
 *  ``unifideckGameCache``. Idempotent — safe to call from both
 *  the eager plugin-init path and the QAM-mount path. */
export async function loadUnifideckCache(): Promise<void> {
  try {
    const raw = await call<[], unknown>("get_all_unifideck_games");
    // Backend wraps every response in ``{success, error, data}``
    // — unwrap so we end up with the actual list of games.
    const games = unwrapRpcEnvelope<RpcGameRow[] | null | undefined>(raw, {
      route: "get_all_unifideck_games",
    });
    const inputs: UnifideckGameInput[] = [];
    const counts: Record<Exclude<StoreSlug, "steam">, number> = {
      epic: 0,
      gog: 0,
      amazon: 0,
      ubisoft: 0,
      microsoft: 0,
    };
    for (const g of games ?? []) {
      if (g.app_id == null) continue;
      if (!isNonSteamStore(g.store)) continue;
      const steamAppId =
        typeof g.metadata?.steam_app_id === "number"
          ? (g.metadata.steam_app_id as number)
          : undefined;
      inputs.push({
        appId: g.app_id,
        store: g.store,
        isInstalled: Boolean(g.installed),
        steamAppId,
      });
      counts[g.store] += 1;
    }
    updateUnifideckCache(inputs);
    storeCountSink?.(counts);
  } catch (e) {
    console.error("[Unifideck] loadUnifideckCache failed", e);
  }
}

/** Kick off the eager load + subscribe to sync-completed events
 *  for refresh. Idempotent — calls after the first are no-ops. */
export function startUnifideckCacheAutoload(): void {
  if (cacheLoadStarted) return;
  cacheLoadStarted = true;
  void loadUnifideckCache();
  window.addEventListener("unifideck-sync-completed", () => {
    void loadUnifideckCache();
  });
  // ShortcutService emits SHORTCUT_INSTALL_STATE_CHANGED on
  // post-install/uninstall — flip the per-app entry immediately so
  // the GOG tab and detail-page UI react without waiting for the
  // next full library reload.
  EventBusClient.subscribe(Events.SHORTCUT_INSTALL_STATE_CHANGED, (kw) => {
    const appId = kw.app_id;
    const store = kw.store;
    const installed = kw.installed;
    if (typeof appId !== "number") return;
    if (typeof store !== "string") return;
    if (typeof installed !== "boolean") return;
    if (!isNonSteamStore(store as StoreSlug)) return;
    updateSingleGameStatus({
      appId,
      store: store as Exclude<StoreSlug, "steam">,
      isInstalled: installed,
    });
  });
}
