/**
 * Download queue DTOs.
 *
 * Mirrors the backend `services/download/models.py`. The
 * `DownloadQueueInfo` shape is what the `get_download_queue`
 * RPC returns; the queue snapshot is reactive on the frontend
 * via `DownloadContext` (Phase F2).
 */
import type { Result, StoreId } from "./api";

/**
 * High-level state of a download item. Drives the badge
 * shown next to the row in DownloadsTab.
 */
export type DownloadStatus =
  | "queued"
  | "downloading"
  | "completed"
  | "cancelled"
  | "error";

/**
 * Sub-status used while `status === "downloading"` to show
 * what the underlying CLI is currently doing.
 */
export type DownloadPhase =
  | "downloading"
  | "extracting"
  | "verifying"
  | "complete";

/**
 * Where the game install lives. `internal` = eMMC, `sdcard`
 * = removable, `custom` = user-picked path on either disk.
 */
export type StorageLocation = "internal" | "sdcard" | "custom";

/**
 * One row of the download queue — kept loosely coupled to the
 * backend `DownloadItem` dataclass so the wire shape stays
 * snake_case / camelCase neutral.
 */
export interface DownloadItem {
  id: string;
  game_id: string;
  game_title: string;
  store: StoreId;
  status: DownloadStatus;
  progress_percent: number;
  downloaded_bytes: number;
  total_bytes: number;
  speed_mbps: number;
  eta_seconds: number;
  added_time: number;
  storage_location: StorageLocation;
  start_time?: number;
  end_time?: number;
  error_message?: string;
  download_phase?: DownloadPhase;
  phase_message?: string;
}

/**
 * Snapshot returned by `get_download_queue` : current item,
 * pending queue, finished history, and overall queue state.
 */
export interface DownloadQueueInfo extends Result {
  current: DownloadItem | null;
  queued: DownloadItem[];
  finished: DownloadItem[];
  state: "idle" | "running";
}

/**
 * Description of one storage option offered to the user when
 * picking where to install : path, free space, label.
 */
export interface StorageLocationInfo {
  id: StorageLocation;
  label: string;
  path: string;
  available: boolean;
  free_space_gb: number;
}

/**
 * Wrapped response of `get_storage_locations`. The wrapper
 * carries the typed `Result` outcome alongside the data so
 * callers don't need a second flag for errors.
 */
export interface StorageLocationsResponse extends Result {
  locations: StorageLocationInfo[];
  default: StorageLocation;
}

/**
 * Compact flag returned by `is_downloading` to tell the UI
 * whether a download is in progress without paying for the
 * full queue snapshot.
 */
export interface IsDownloadingResponse extends Result {
  is_downloading: boolean;
  download_info?: DownloadItem;
}
