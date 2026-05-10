/**
 * Pure formatting helpers used across the downloads UI.
 *
 * Both functions are total: they return a non-empty string
 * for every numeric input including 0 and negatives, so
 * callers don't need defensive null/empty handling around
 * them.
 */

/** Format a byte count to a human-readable size with up to
 *  two fractional digits. `1024` → "1 KB", `1500` → "1.46 KB". */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

/** Format a duration in seconds to "M:SS" or "H:MM:SS".
 *  Non-positive inputs return "--:--" (used by the downloads
 *  list when an ETA hasn't been computed yet). */
export function formatETA(seconds: number): string {
  if (seconds <= 0) return "--:--";
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, "0")}:${secs
      .toString()
      .padStart(2, "0")}`;
  }

  return `${mins}:${secs.toString().padStart(2, "0")}`;
}
