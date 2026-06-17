/**
 * Steam Store spoofing for non-Steam Unifideck shortcuts.
 *
 * Ported from `staging:src/spoofing/SteamStorePatcher.ts`.
 *
 * Strategy: monkey-patch three Steam getter methods so that when
 * Steam asks for the overview / details of a Unifideck shortcut
 * (synthetic AppID), we return the real Steam Store data for the
 * matched real-Steam-AppID instead. The mappings and rich
 * `appdetails` JSON are pre-fetched by the backend
 * `MetadataService` during sync and read here via two RPCs.
 *
 *   - `get_real_steam_appid_mappings` → `{shortcut_id: real_id}`
 *   - `get_steam_metadata_cache`      → `{real_id: appdetails}`
 *
 * Net effect: Epic / GOG / Amazon / Ubisoft shortcuts in Steam's
 * library show real cover art, store descriptions, Metacritic
 * scores, etc. Launch behaviour is unchanged — clicking Play
 * still routes through the GameAction interceptor to the actual
 * launcher.
 */
import { call } from "@decky/api";
import { rpcRoutes } from "../../api/rpc-routes";

let steamAppIdMappings: Record<number, number> = {};
let storeDataCache: Record<number, AppOverview> = {};
let appDetailsCache: Record<number, AppDetails> = {};
const patchedOverviews = new Set<number>();

/** Resolves once `loadFromBackend()` has completed (mappings +
 *  metadata cache populated). `injectGameToAppinfo` awaits this
 *  so that navigation to a game page before the async init has
 *  finished still triggers artwork injection as soon as the data
 *  is ready. */
let _backendLoadPromise: Promise<void> | null = null;

interface BackendMappingsResponse {
  success: boolean;
  mappings: Record<string, number>;
}

interface BackendMetadataResponse {
  success: boolean;
  metadata: Record<string, AppDetailsRaw>;
}

interface AppDetailsRaw {
  name?: string;
  short_description?: string;
  detailed_description?: string;
  about_the_game?: string;
  developers?: string[];
  publishers?: string[];
  release_date?: { date?: string };
  header_image?: string;
  capsule_image?: string;
  controller_support?: "full" | "partial" | string;
  metacritic?: { score?: number; url?: string };
  recommendations?: { total?: number };
  categories?:
    | Array<{ id: number; description?: string }>
    | Record<string, number>;
  genres?: Array<{ id: number; description?: string }> | Record<string, number>;
  screenshots?: Array<{ path_full?: string }>;
  platforms?: { windows?: boolean; mac?: boolean; linux?: boolean };
  supported_languages?: string | Record<string, number>;
  achievements?: { total?: number };
  dlc?: number[];
  website?: string;
}

interface AppOverview extends Record<string, unknown> {
  appid: number;
  display_name: string;
  app_type: number;
  visible_in_game_list: boolean;
  BIsShortcut?: () => boolean;
  BIsModOrShortcut?: () => boolean;
  GameID?: () => string;
}

interface AppDetails extends Record<string, unknown> {
  unAppID: number;
  strDisplayName: string;
}

interface AppStoreLike {
  GetAppOverviewByAppID: (id: number) => AppOverview | null;
  m_mapApps?: Map<number, AppOverview>;
}

interface AppDetailsStoreLike {
  GetAppDetails: (id: number) => AppDetails | null;
  GetAppData?: (id: number) => unknown;
}

function getAppStore(): AppStoreLike | null {
  return (window as unknown as { appStore?: AppStoreLike }).appStore ?? null;
}

function getAppDetailsStore(): AppDetailsStoreLike | null {
  return (
    (window as unknown as { appDetailsStore?: AppDetailsStoreLike })
      .appDetailsStore ?? null
  );
}

function toUnsignedAppId(id: number): number {
  return id < 0 ? id + 0x100000000 : id;
}

function toSignedAppId(id: number): number {
  return id > 0x7fffffff ? id - 0x100000000 : id;
}

function extractIds(
  data: AppDetailsRaw["categories"] | AppDetailsRaw["genres"],
): number[] {
  if (!data) return [];
  if (Array.isArray(data)) {
    return data
      .map((x) => x.id)
      .filter((id): id is number => typeof id === "number");
  }
  return Object.keys(data)
    .filter((k) => k.startsWith("category_") || k.startsWith("genre_"))
    .map((k) => Number(k.split("_")[1]))
    .filter((n) => !Number.isNaN(n));
}

function extractLanguages(
  data: AppDetailsRaw["supported_languages"],
): Array<{ strLanguageName: string }> {
  if (!data) return [];
  if (typeof data === "string") {
    return data.split(",").map((l) => ({ strLanguageName: l.trim() }));
  }
  return Object.keys(data).map((lang) => ({
    strLanguageName: lang.charAt(0).toUpperCase() + lang.slice(1),
  }));
}

function buildOverview(steamAppId: number, raw: AppDetailsRaw): AppOverview {
  const rtRelease = raw.release_date?.date
    ? Math.floor(new Date(raw.release_date.date).getTime() / 1000)
    : 0;
  const controllerSupport =
    raw.controller_support === "full"
      ? 2
      : raw.controller_support === "partial"
      ? 1
      : 0;
  return {
    appid: steamAppId,
    display_name: raw.name ?? "",
    app_type: 1,
    visible_in_game_list: true,
    sort_as: raw.name?.toLowerCase() ?? "",
    rt_original_release_date: rtRelease,
    rt_steam_release_date: rtRelease,
    controller_support: controllerSupport,
    steam_deck_compat_category: 0,
    store_tag: extractIds(raw.genres),
    store_category: extractIds(raw.categories),
    metacritic_score: raw.metacritic?.score ?? 0,
    icon_hash: "",
    header_filename: raw.header_image ?? "",
    library_capsule_filename: raw.capsule_image ?? "",
    BIsShortcut: () => false,
    BIsModOrShortcut: () => false,
    GameID: () => String(steamAppId),
    __from_web_api: true,
  };
}

function buildDetails(steamAppId: number, raw: AppDetailsRaw): AppDetails {
  const developers = raw.developers ?? [];
  const publishers = raw.publishers ?? [];
  const rtRelease = raw.release_date?.date
    ? Math.floor(new Date(raw.release_date.date).getTime() / 1000)
    : 0;
  const cloud = extractIds(raw.categories).includes(23);
  return {
    unAppID: steamAppId,
    strDisplayName: raw.name ?? "",
    strDeveloperName: developers[0] ?? "",
    strHomepageURL: raw.website ?? "",
    strDescription: raw.short_description ?? "",
    strFullDescription: raw.detailed_description ?? raw.about_the_game ?? "",
    associations: {
      rgDevelopers: developers.map((d) => ({ strName: d, strURL: "" })),
      rgPublishers: publishers.map((p) => ({ strName: p, strURL: "" })),
      rgFranchises: [],
    },
    rtReleaseDate: rtRelease,
    vecPlatforms: [
      raw.platforms?.windows && "windows",
      raw.platforms?.mac && "osx",
      raw.platforms?.linux && "linux",
    ].filter((p): p is string => Boolean(p)),
    vecLanguages: extractLanguages(raw.supported_languages),
    bCloudAvailable: cloud,
    bCloudEnabledForApp: cloud,
    achievements: {
      nAchieved: 0,
      nTotal: raw.achievements?.total ?? 0,
      vecAchievedHidden: [],
      vecHighlight: [],
      vecUnachieved: [],
    },
    eSteamInputControllerMask:
      raw.controller_support === "full"
        ? 2
        : raw.controller_support === "partial"
        ? 1
        : 0,
    vecDLC: (raw.dlc ?? []).map((id) => ({
      appid: id,
      strName: "",
      bInstalled: false,
    })),
    nScreenshots: raw.screenshots?.length ?? 0,
    lDiskSpaceRequiredBytes: 0,
    vecDeckCompatTestResults: [],
    __from_web_api: true,
  };
}

interface OverviewMutable extends AppOverview {
  TriggerChange?: () => void;
}

function injectMetadataIntoOverview(overview: OverviewMutable): boolean {
  if (!overview) return false;
  const rawAppId =
    typeof overview.appid === "function"
      ? (overview.appid as () => number)()
      : overview.appid;
  if (typeof rawAppId !== "number") return false;
  if (patchedOverviews.has(rawAppId)) return false;
  const signed = toSignedAppId(rawAppId);
  const realId = steamAppIdMappings[signed] ?? steamAppIdMappings[rawAppId];
  if (!realId) return false;
  const details = appDetailsCache[realId];
  if (!details) return false;
  if (
    typeof details.strDeveloperName === "string" &&
    details.strDeveloperName
  ) {
    overview.developer = details.strDeveloperName;
    overview.strDeveloperName = details.strDeveloperName;
  }
  const publishers = (
    details.associations as
      | {
          rgPublishers?: Array<{ strName?: string }>;
        }
      | undefined
  )?.rgPublishers;
  if (publishers?.[0]?.strName) {
    overview.publisher = publishers[0].strName;
    overview.strPublisherName = publishers[0].strName;
  }
  if (typeof details.strDescription === "string") {
    overview.short_description = details.strDescription;
    overview.strShortDescription = details.strDescription;
  }
  if (typeof details.rtReleaseDate === "number") {
    overview.rt_original_release_date = details.rtReleaseDate;
    overview.rt_steam_release_date = details.rtReleaseDate;
  }
  patchedOverviews.add(rawAppId);
  try {
    overview.TriggerChange?.();
  } catch {
    /* ignore */
  }
  return true;
}

async function loadFromBackend(): Promise<void> {
  try {
    const mappingsResp = await call<[], BackendMappingsResponse>(
      rpcRoutes.getRealSteamAppidMappings,
    );
    if (mappingsResp?.success && mappingsResp.mappings) {
      const out: Record<number, number> = {};
      for (const [k, v] of Object.entries(mappingsResp.mappings)) {
        const key = Number(k);
        if (!Number.isNaN(key)) out[key] = v;
      }
      steamAppIdMappings = out;
    }
  } catch (e) {
    console.error("[Unifideck Store Patch] failed to load mappings:", e);
  }
  if (Object.keys(steamAppIdMappings).length === 0) return;
  try {
    const metaResp = await call<[], BackendMetadataResponse>(
      rpcRoutes.getSteamMetadataCache,
    );
    if (metaResp?.success && metaResp.metadata) {
      for (const [k, raw] of Object.entries(metaResp.metadata)) {
        const steamId = Number(k);
        if (Number.isNaN(steamId)) continue;
        storeDataCache[steamId] = buildOverview(steamId, raw);
        appDetailsCache[steamId] = buildDetails(steamId, raw);
      }
    }
  } catch (e) {
    console.error("[Unifideck Store Patch] failed to load metadata:", e);
  }
}

/** Force a single shortcut's in-memory overview to be re-spoofed.
 *  Called from the AppDetails route patch when the user opens the
 *  details page of a Unifideck shortcut. */
export function forceInjectMetadataForShortcut(shortcutAppId: number): boolean {
  const appStore = getAppStore();
  const unsigned = toUnsignedAppId(shortcutAppId);
  patchedOverviews.delete(unsigned);
  const overview = appStore?.m_mapApps?.get(unsigned);
  if (!overview) return false;
  return injectMetadataIntoOverview(overview);
}

/** Fire-and-forget : also persist the spoofed metadata via the
 *  backend `inject_game_to_appinfo` RPC so the spoofing survives a
 *  Steam restart. The current backend handler is a no-op stub. */
export async function injectGameToAppinfo(
  shortcutAppId: number,
): Promise<void> {
  // Wait for the async backend load to finish so that
  // steamAppIdMappings is populated. On first navigation this
  // may still be in-flight; on subsequent calls the cached
  // promise resolves immediately.
  if (_backendLoadPromise) await _backendLoadPromise;
  if (!steamAppIdMappings[shortcutAppId]) return;
  forceInjectMetadataForShortcut(shortcutAppId);
  try {
    await call<[number], unknown>(rpcRoutes.injectGameToAppinfo, shortcutAppId);
  } catch (e) {
    console.error("[Unifideck Store Patch] inject_game_to_appinfo failed:", e);
  }
}

interface PatchHandle {
  remove(): void;
}

/**
 * Apply the Steam-store patches. Returns a handle whose `remove()`
 * restores the original Steam getters. Call from the plugin entry
 * after `SteamBridge` is constructed.
 */
export async function applyAppStorePatch(): Promise<PatchHandle> {
  _backendLoadPromise = loadFromBackend();
  await _backendLoadPromise;
  const appStore = getAppStore();
  const appDetailsStore = getAppDetailsStore();
  if (!appStore || !appDetailsStore) {
    console.warn(
      "[Unifideck Store Patch] appStore / appDetailsStore unavailable",
    );
    return { remove: () => {} };
  }
  const origGetOverview = appStore.GetAppOverviewByAppID.bind(appStore);
  const origGetDetails = appDetailsStore.GetAppDetails.bind(appDetailsStore);
  const origGetData = appDetailsStore.GetAppData?.bind(appDetailsStore);

  appStore.GetAppOverviewByAppID = (id: number) => {
    const realId = steamAppIdMappings[id];
    if (!realId) return origGetOverview(id);
    void injectGameToAppinfo(id);
    const owned = origGetOverview(realId);
    if (owned) return owned;
    return storeDataCache[realId] ?? origGetOverview(id);
  };
  appDetailsStore.GetAppDetails = (id: number) => {
    const realId = steamAppIdMappings[id];
    if (!realId) return origGetDetails(id);
    const owned = origGetDetails(realId);
    if (owned) return owned;
    return appDetailsCache[realId] ?? origGetDetails(id);
  };
  if (origGetData) {
    appDetailsStore.GetAppData = (id: number) => {
      const realId = steamAppIdMappings[id];
      if (!realId) return origGetData(id);
      const owned = origGetData(realId);
      if (owned) return owned;
      const overview = storeDataCache[realId];
      const details = appDetailsCache[realId];
      if (overview || details) return { overview, details };
      return origGetData(id);
    };
  }

  console.log(
    `[Unifideck Store Patch] active — ${
      Object.keys(steamAppIdMappings).length
    } mappings, ${Object.keys(appDetailsCache).length} metadata entries`,
  );

  return {
    remove: () => {
      appStore.GetAppOverviewByAppID = origGetOverview;
      appDetailsStore.GetAppDetails = origGetDetails;
      if (origGetData) appDetailsStore.GetAppData = origGetData;
      steamAppIdMappings = {};
      storeDataCache = {};
      appDetailsCache = {};
      patchedOverviews.clear();
    },
  };
}
