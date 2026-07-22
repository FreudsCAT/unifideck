/**
 * download-errors — map a backend download-failure code/message to a
 * friendly, localized string.
 *
 * The backend folds the store CLI's real error tail into
 * `error_message` (e.g. `insufficient_space:need=66.4GB,free=43.5GB` from
 * the size-aware preflight, or `legendary_exit_1: … Not enough available
 * disk space! …` when legendary itself aborts). Both the failure toast
 * (`download-store`) and the failed-row detail (`DownloadItemRow`) render
 * through this one helper so they always agree.
 *
 * Matching keys off the RAW message text, not the backend `error_type`:
 * `classify_download_error` historically missed legendary's "disk space"
 * phrasing, so the type is unreliable for the disk case. The final
 * branch echoes the raw tail (minus a `legendary_exit_N:` prefix) so an
 * unrecognized failure is never silent again.
 */
import type { TFunction } from "i18next";

/** Pull `need`/`free` GB out of an `insufficient_space:…` code. */
const SPACE_RE = /need=([\d.]+)GB,free=([\d.]+)GB/;
/** Strip a leading machine prefix like `legendary_exit_1: ` / `gogdl_exit_2: `. */
const EXIT_PREFIX_RE = /^\w+_exit_-?\d+:\s*/;

export function friendlyDownloadError(
  raw: string | undefined,
  t: TFunction,
): string {
  if (!raw || !raw.trim()) {
    return t("errors.download.generic");
  }

  const lower = raw.toLowerCase();

  if (raw.startsWith("insufficient_space:")) {
    const m = SPACE_RE.exec(raw);
    if (m) {
      return t("errors.download.insufficientSpace", {
        need: m[1],
        free: m[2],
      });
    }
    return t("errors.download.diskSpace");
  }

  if (
    lower.includes("disk space") ||
    lower.includes("no space") ||
    lower.includes("disk full") ||
    raw.startsWith("low_space:")
  ) {
    return t("errors.download.diskSpace");
  }

  if (lower.includes("network") || lower.includes("connection")) {
    return t("errors.download.network");
  }

  if (lower.includes("login") || lower.includes("auth")) {
    return t("errors.download.authExpired");
  }

  // Unknown failure — surface the real tail so nothing is ever silent.
  return raw.replace(EXIT_PREFIX_RE, "").trim() || t("errors.download.generic");
}
