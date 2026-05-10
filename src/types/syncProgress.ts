/**
 * Library sync progress payload.
 *
 * Emitted by the backend SyncService and consumed by the
 * sync progress bar in `<QuickAccessPanel>`. The shape is
 * append-only — fields are added when new sync stages land,
 * but never removed mid-version.
 */
export interface SyncProgressCurrentGame {
  label: string;
  values: Record<string, string | number>;
}

/**
 * Live snapshot of the active sync : current store, total
 * vs done count, optional ETA. Polled by the SyncContext
 * provider while a sync is running.
 */
export interface SyncProgress {
  total_games: number;
  synced_games: number;
  current_game: SyncProgressCurrentGame;
  status: string;
  progress_percent: number;
  error?: string;
  // Artwork tracking
  artwork_total?: number;
  artwork_synced?: number;
  current_phase?: string;
  // Steam / RAWG metadata tracking
  steam_total?: number;
  steam_synced?: number;
  rawg_total?: number;
  rawg_synced?: number;
  // UnifiDB metadata tracking
  unifidb_total?: number;
  unifidb_synced?: number;
  // Metacritic tracking
  metacritic_total?: number;
  metacritic_synced?: number;
  // Lifecycle flags
  restart_pending?: boolean;
  is_cancelling?: boolean;
  request_source?: string;
  run_id?: number;
  started_at?: number;
  finished_at?: number;
}
