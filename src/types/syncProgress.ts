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
  // Per-source metadata tracking (incremented in lockstep by
  // MetadataService._run_enrichment — three sources run in
  // parallel via asyncio.gather; one row per source on the UI).
  steam_total?: number;
  steam_synced?: number;
  unifidb_total?: number;
  unifidb_synced?: number;
  metacritic_total?: number;
  metacritic_synced?: number;
  // Compatibility phase (proton_meta) — ProtonDB tier +
  // Deck-Verified status per game. Backend tracks this in
  // SyncProgress.compat_total / compat_synced.
  compat_total?: number;
  compat_synced?: number;
  // Lifecycle flags
  restart_pending?: boolean;
  is_cancelling?: boolean;
  request_source?: string;
  run_id?: number;
  started_at?: number;
  finished_at?: number;
}
