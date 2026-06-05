/**
 * Layout primitives + shared styles for the Play section
 * variants, matched 1:1 with the staging
 * ``PlayButtonOverride`` reference so the deck UX stays
 * identical across the refactor.
 *
 * Structure (single horizontal row, direct children of
 * ``<Focusable>``):
 *
 *   [ primary action ]   [ meta block ]   [ icon group → ]
 *
 * Meta is inline (label+value pairs side by side), icons
 * float to the right via ``marginLeft: auto``.
 */
import { CSSProperties, FC, ReactNode, useEffect, useState } from "react";
import { Focusable } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameSize } from "../../hooks/useGameSize";
import { PLAY_FOCUS_CSS } from "./play.css";

/** Inline style shared by every primary action button
 *  (Install / Play / Resume / Update / Cancel). Matches
 *  staging's ``actionBtnStyle``. */
export const actionBtnStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-start",
  gap: 8,
  flex: "0 0 auto",
  width: "auto",
  minWidth: 200,
  height: 48,
  padding: "0 24px",
  color: "#fff",
  fontSize: 16,
  fontWeight: 500,
  borderRadius: 4,
  border: "none",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

/** Inline style for square icon buttons (controller / settings
 *  / X). Matches staging's icon DialogButton inline style. */
export const iconBtnStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: 48,
  height: 48,
  minWidth: 48,
  padding: 0,
  background: "rgba(255, 255, 255, 0.1)",
  borderRadius: 4,
};

/**
 * Outer Focusable shell for every play variant. Marks the
 * subtree with ``data-unifideck-play-wrapper="true"`` so the
 * backend CDP hide can skip our own DOM when walking ancestors.
 * Styling matches staging: full-width flex row with the
 * translucent dark background and 16 px inner padding.
 */
export const PlayShell: FC<{ children: ReactNode }> = ({ children }) => (
  <Focusable
    flow-children="row"
    data-unifideck-play-wrapper="true"
    style={{
      display: "flex",
      alignItems: "center",
      width: "100%",
      padding: "16px",
      boxSizing: "border-box",
      background: "rgba(14, 20, 27, 0.33)",
      position: "relative",
      zIndex: 2,
    }}
  >
    {/* Render the focus CSS inline so it lands in THIS (App-Details)
        CEF document — a document.head injection from elsewhere does
        not reach it. This is what makes Install→blue / Play→green /
        Cancel→red actually apply on focus (staging's pattern). */}
    <style>{PLAY_FOCUS_CSS}</style>
    {children}
  </Focusable>
);

/** Right-floated icon group. ``marginLeft: auto`` pushes it
 *  to the far right of the row. */
export const IconGroup: FC<{ children: ReactNode }> = ({ children }) => (
  <div
    style={{
      display: "flex",
      gap: 8,
      marginLeft: "auto",
      flex: "0 0 auto",
    }}
  >
    {children}
  </div>
);

/** Format bytes as a human-readable size string. */
export function formatBytes(bytes: number | undefined | null): string {
  if (!bytes || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

/** Format a Unix timestamp (seconds) as a date string. */
export function formatLastPlayed(rtLastTimePlayed: number | undefined | null): string {
  if (!rtLastTimePlayed || rtLastTimePlayed <= 0) return "—";
  const d = new Date(rtLastTimePlayed * 1000);
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** One label/value column inside {@link MetaInline}. */
const MetaItem: FC<{ label: string; value: string }> = ({ label, value }) => (
  <div>
    <div
      style={{
        fontSize: 11,
        fontWeight: 600,
        textTransform: "uppercase",
        color: "#8f98a0",
        letterSpacing: "0.08em",
        lineHeight: 1,
        marginBottom: 5,
      }}
    >
      {label}
    </div>
    <div style={{ fontSize: 14, color: "#dcdedf" }}>{value}</div>
  </div>
);

interface MetaInlineProps {
  /** Total install size in bytes (Space Required column). */
  sizeBytes?: number;
  /** Whether to render the Last Played column (installed games only). */
  showLastPlayed?: boolean;
  /** Steam appId — used to look up rtLastTimePlayed. */
  appId?: number | null;
  /** True for installed games. Switches the size label to
   *  "Installed Size" and makes {@link useGameSize} report the
   *  on-disk size instead of the (stale) pre-install download size. */
  installed?: boolean;
}

/**
 * Inline metadata block between the primary action button
 * and the icon group. Side-by-side "Space Required" /
 * "Last Played" pairs, with ``marginLeft: 20`` to space it
 * off the action button (matches staging spacing).
 */
export const MetaInline: FC<MetaInlineProps> = ({
  sizeBytes, showLastPlayed = false, appId, installed = false,
}) => {
  const { t } = useTranslation();
  const [lastPlayed, setLastPlayed] = useState<number | null>(null);
  // Size is fetched out-of-band (see useGameSize) so a slow store
  // lookup never blocks this row from rendering. Keyed on `installed`
  // so the on-disk size replaces the pre-install download size once
  // the game finishes installing. Prefer the fetched value; fall back
  // to any size the caller already had.
  const fetchedSize = useGameSize(appId ?? null, installed);
  const resolvedSize = fetchedSize && fetchedSize > 0 ? fetchedSize : sizeBytes;

  useEffect(() => {
    if (!showLastPlayed || appId == null) return;
    let cancelled = false;
    const apps = (window as unknown as {
      SteamClient?: { Apps?: { GetPlaytime?: (id: number) => Promise<{ rtLastTimePlayed?: number }> } };
    }).SteamClient?.Apps;
    if (!apps?.GetPlaytime) return;
    apps.GetPlaytime(appId).then((res) => {
      if (cancelled) return;
      setLastPlayed(res?.rtLastTimePlayed ?? null);
    }).catch(() => { /* ignore */ });
    return () => { cancelled = true; };
  }, [appId, showLastPlayed]);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 32,
        marginLeft: 20,
        flex: "0 1 auto",
      }}
    >
      <MetaItem
        label={installed ? t("playMeta.installedSize") : t("playMeta.spaceRequired")}
        value={formatBytes(resolvedSize)}
      />
      {showLastPlayed && (
        <MetaItem
          label={t("playMeta.lastPlayed")}
          value={lastPlayed ? formatLastPlayed(lastPlayed) : t("playMeta.neverPlayed")}
        />
      )}
    </div>
  );
};
