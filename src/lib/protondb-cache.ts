/**
 * ProtonDB & Steam Deck compat cache.
 *
 * Synchronous in-memory lookup populated once from the backend
 * `get_protondb_cache` RPC. The cache feeds two surfaces :
 *  - the `deckCompat` library-tab filter (synchronous)
 *  - the Deck Verified / ProtonDB badges in the game-info panel
 *
 * Ported from staging:src/tabs/protondb.ts. Uses `call` directly
 * because the loader runs once at module-init time, outside any
 * React tree (no hook context available).
 */
import { call } from "@decky/api";
import { rpcRoutes } from "../api/rpc-routes";

export type ProtonDBTier =
  | "platinum"
  | "gold"
  | "silver"
  | "bronze"
  | "borked"
  | "pending"
  | "native";

export type DeckVerifiedStatus =
  | "verified"
  | "playable"
  | "unsupported"
  | "unknown";

export interface GameCompatInfo {
  tier: ProtonDBTier | null;
  deckVerified: DeckVerifiedStatus;
  steamAppId: number | null;
}

interface CompatCacheEntry extends GameCompatInfo {
  timestamp: number;
}

/** Title-keyed cache (Epic/GOG/Amazon/Ubisoft/Microsoft games). */
const compatCache = new Map<string, CompatCacheEntry>();

/** AppId-keyed cache (native Steam games). */
const protonDBCache = new Map<
  number,
  { tier: ProtonDBTier; timestamp: number }
>();

let cacheLoadedFromBackend = false;

function normalizeTitle(title: string): string {
  return title.toLowerCase().trim();
}

/** Backend payload shape — keys are str(app_id), values come from
 *  `CompatRating.to_dict()` (protondb_tier, deck_status, title). */
interface BackendCompatEntry {
  appid?: number | null;
  title?: string;
  protondb_tier?: string | null;
  deck_status?: string;
  sources?: string[];
}

/**
 * Load the compat cache from the backend once. Idempotent — safe to
 * call multiple times during boot. Catches every error so a backend
 * outage degrades to "no badges, deckCompat tab empty" rather than
 * crashing module init.
 */
export async function loadCompatCacheFromBackend(force = false): Promise<void> {
  if (cacheLoadedFromBackend && !force) return;
  try {
    const raw = await call<[], Record<string, BackendCompatEntry>>(
      rpcRoutes.getProtondbCache,
    );
    if (!raw || typeof raw !== "object") {
      cacheLoadedFromBackend = true;
      return;
    }
    // A forced reload (post-sync) repopulates from scratch so entries
    // dropped upstream don't linger.
    if (force) {
      compatCache.clear();
      protonDBCache.clear();
    }
    for (const [key, entry] of Object.entries(raw)) {
      if (!entry || typeof entry !== "object") continue;
      const tier = (entry.protondb_tier ?? null) as ProtonDBTier | null;
      const deckVerified = (entry.deck_status ??
        "unknown") as DeckVerifiedStatus;
      const steamAppId =
        typeof entry.appid === "number" ? entry.appid : Number(key);
      const ts = Date.now();
      const titleKey = entry.title ? normalizeTitle(entry.title) : null;
      const value: CompatCacheEntry = {
        tier,
        deckVerified,
        steamAppId: Number.isFinite(steamAppId) ? steamAppId : null,
        timestamp: ts,
      };
      if (titleKey) compatCache.set(titleKey, value);
      if (Number.isFinite(steamAppId) && tier) {
        protonDBCache.set(steamAppId, { tier, timestamp: ts });
      }
    }
    cacheLoadedFromBackend = true;
    console.log(
      `[Unifideck] Loaded ${compatCache.size} title + ${protonDBCache.size} appId compat entries`,
    );
  } catch (e) {
    console.error("[Unifideck] loadCompatCacheFromBackend failed", e);
  }
}

/** Synchronous ProtonDB tier lookup by Steam appId (native games). */
export function getCachedRating(appId: number): ProtonDBTier | null {
  return protonDBCache.get(appId)?.tier ?? null;
}

/** Synchronous compat info lookup by game title (non-Steam games). */
export function getCachedCompatByTitle(title: string): GameCompatInfo | null {
  const cached = compatCache.get(normalizeTitle(title));
  if (!cached) return null;
  return {
    tier: cached.tier,
    deckVerified: cached.deckVerified,
    steamAppId: cached.steamAppId,
  };
}

/**
 * "Great on Deck" = Verified / Playable on Deck, OR Native / Platinum on
 * ProtonDB. Gold-only without Deck verification fails — matches staging.
 */
export function meetsGreatOnDeckCriteria(
  compat: GameCompatInfo | null,
): boolean {
  if (!compat) return false;
  if (
    compat.deckVerified === "verified" ||
    compat.deckVerified === "playable"
  ) {
    return true;
  }
  if (compat.tier === "native" || compat.tier === "platinum") return true;
  return false;
}

export function isCompatCacheLoaded(): boolean {
  return cacheLoadedFromBackend;
}

export function getCompatCacheSize(): number {
  return compatCache.size;
}
