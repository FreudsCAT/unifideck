/**
 * Backend RPC contract — TypeScript mirror of `core/types/`.
 *
 * Every dataclass exposed via `to_dict()` on the Python side
 * has its TS interface here. Field names use the wire format
 * (snake_case) so JSON parsing is a no-op cast — no runtime
 * adapter, no field rename pass.
 *
 * If a field is added on the backend dataclass, it MUST be
 * added here in the same PR that lands the backend change.
 * The contract is enforced by reviewers, not by tooling
 * (TypeScript can't see Python).
 */
/** A single Steam Deck verification test result row in the
 *  compatibility details modal. ``passed === true`` renders a
 *  green checkmark; ``false`` renders a yellow warning. */
export interface DeckTestResult {
  text: string;
  passed: boolean;
}

/** Rich display metadata for the game info panel — sourced from
 *  Steam Store appdetails (preferred), UnifiDB, and Metacritic
 *  (fallback). Returned by ``get_game_metadata_display``. Kept
 *  separate from {@link Game} so install-state and
 *  display-metadata can be cached and refreshed independently. */
export interface GameMetadata {
  /** Real Steam App ID when the shortcut was resolved to a Steam
   *  store entry, ``0`` otherwise. Gates the steam:// nav buttons. */
  steam_app_id: number;
  /** True when ``steam_app_id`` corresponds to a real Steam Store
   *  page (validated against the cached appdetails payload). */
  has_steam_store_page: boolean;
  store: StoreId;
  /** Third-party store landing URL — used when no Steam page exists. */
  store_url: string;
  title: string;
  developer: string;
  publisher: string;
  release_date: string;
  metacritic: number | null;
  description: string;
  /** ``0`` unknown, ``1`` unsupported, ``2`` playable, ``3`` verified. */
  deck_compatibility: 0 | 1 | 2 | 3;
  deck_test_results: DeckTestResult[];
  genres: string[];
  homepage_url?: string;
  /** Whether THIS store's copy of the game has native cloud saves.
   *  ``null``/absent = unknown (no enriched entry), and the UI stays quiet
   *  rather than claiming an absence. Known before the game is installed, so
   *  it can inform which storefront's copy to download. */
  cloud_saves?: boolean | null;
}

/** Universal `Game` representation aggregated from any store. */
export interface Game {
  id: string;
  store_game_id: string;
  title: string;
  store: StoreId;
  /** Adapter-normalised install flag (set by ``adaptGame`` on the
   *  app-details path). NOTE: raw rows straight off
   *  ``get_all_unifideck_games`` do NOT carry this — they carry
   *  ``installed`` (the wire field, below). Read ``installed ?? is_installed``
   *  when consuming un-adapted rows. */
  is_installed: boolean;
  /** Raw wire field from ``asdict(Game)`` (backend ``Game.installed``).
   *  Present on un-adapted RPC rows; ``adaptGame`` folds it into
   *  ``is_installed``. */
  installed?: boolean;
  cover_image?: string;
  install_path?: string;
  executable?: string;
  app_id?: number;
  steam_app_id?: number;
  ownership_type?: OwnershipType;
  store_tags?: GameTag[];
  size_bytes?: number;
  deck_rating?: DeckRating;
}

/** One achievement (definition + this user's unlock status). */
export interface Achievement {
  key: string;
  name: string;
  description: string;
  image_unlocked: string;
  image_locked: string;
  hidden: boolean;
  unlocked: boolean;
  /** Epoch seconds the achievement was unlocked, or null if still locked. */
  unlocked_at: number | null;
  rarity?: number | null;
}

/** A game's achievements + summary (from `get_game_achievements`). */
export interface GameAchievements {
  store: StoreId;
  game_id: string;
  total: number;
  unlocked: number;
  percent: number;
  achievements: Achievement[];
}

/** Last play session's unlock summary (from `get_last_session_achievements`). */
export interface LastSessionAchievements {
  names: string[];
  unlocked: number;
  total: number;
  /** Epoch seconds the session ended. */
  at: number;
}

/** Common wrapper for every RPC method's response. */
export interface Result {
  success: boolean;
  error?: string;
}

/** Auth start/complete/logout response. */
export interface AuthResult extends Result {
  url?: string;
  token?: string;
  store: StoreId;
}

/** Install completion response. */
export interface InstallResult extends Result {
  install_path?: string;
  game_id: string;
  size_mb?: number;
  store: StoreId;
}

/** Sync run summary. */
export interface SyncResult extends Result {
  games: Game[];
  store: StoreId;
  count: number;
  duration_ms: number;
}

/** Download progress snapshot. */
export interface DownloadResult extends Result {
  progress: number;
  game_id: string;
  store: StoreId;
  queued: boolean;
}

/** Per-store status block returned by `check_store_status`. */
export interface StoreInfo {
  name: StoreId;
  display_name: string;
  icon: string;
  available: boolean;
  auth_status: StoreStatus;
}

/**
 * Discriminator for which store a Game/Auth/Download
 * payload comes from.
 *
 * The set is closed on purpose : every backend route
 * accepting a store argument validates against this
 * union and rejects anything else. Adding a 6th store
 * therefore requires a coordinated change in both
 * `core/types/events.py` (StoreEnum) and this file.
 */
export type StoreId =
  | "steam"
  | "epic"
  | "gog"
  | "amazon"
  | "microsoft"
  | "ubisoft"
  | "battlenet";

/**
 * Per-store availability + auth state, returned by
 * `check_store_status` RPC. The frontend uses it to
 * decide whether to show a Connect button, a Sync
 * button, or a re-auth prompt.
 *
 *  - `unauthenticated` : no token present
 *  - `authenticated`   : token valid, ready to sync
 *  - `error`           : token rejected by the store API
 *  - `unavailable`     : store CLI / Wine prefix missing
 */
export type StoreStatus = "connected" | "disconnected" | "expired" | "error";

/**
 * How the user owns a given title. Discriminates
 * subscription games (xCloud, Game Pass) from
 * purchased ones, which matters for badge display
 * and uninstall confirmation copy.
 */
export type OwnershipType = "owned" | "subscription" | "trial";

/**
 * Tag attached to a Game by its store. Drives the
 * coloured pill rendered in `GameInfoMetadata`. Tags
 * are additive : a game can carry several at once
 * (e.g. `dlc` + `early-access`).
 */
export type GameTag =
  | "demo"
  | "addon"
  | "dlc"
  | "preorder"
  | "early_access"
  // Xbox Cloud Gaming title — streamed in a browser, never installed.
  // Drives the "Play on Cloud" play-section variant.
  | "xcloud";

/**
 * Steam Deck verification rating, as returned by
 * Valve's Deck Verified compatibility report (or
 * inferred from ProtonDB community grades when
 * Valve has no rating yet).
 */
export type DeckRating = "verified" | "playable" | "unsupported" | "unknown";
