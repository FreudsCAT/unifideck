/**
 * GameInfoMetadata — install path, size, executable, deck
 * compatibility tag.
 *
 * Renders one row per known metadata field. Fields with
 * no value are omitted (so a non-installed game shows just
 * the deck rating, not "Install path: —").
 */
import React, { FC } from "react";
import { Focusable } from "@decky/ui";
import { useTranslation } from "react-i18next";
import type { Game, DeckRating } from "../../types/api";

/** Props. */
interface Props {
  game: Game;
  mode: "compact" | "full";
}

const RATING_COLOR: Record<DeckRating, string> = {
  verified: "#22c55e",
  playable: "#eab308",
  unsupported: "#ef4444",
  unknown: "#94a3b8",
};

/** Format size. */
function formatSize(bytes?: number): string | null {
  if (!bytes) return null;
  const gb = bytes / (1024 ** 3);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 ** 2);
  return `${mb.toFixed(0)} MB`;
}

/** Row. */
const Row: FC<{ label: string; value: string }> = ({ label, value }) => (
  <div style={{ display: "flex", gap: 8, fontSize: 13 }}>
    <span style={{ color: "#94a3b8", minWidth: 110 }}>{label}</span>
    <span style={{ flex: 1, wordBreak: "break-all" }}>{value}</span>
  </div>
);

/**
 * Metadata block of the game-info panel : description,
 * release date, genres, supported features, tag pills.
 * Hidden in compact view mode.
 */
export const GameInfoMetadata: FC<Props> = ({ game, mode }) => {
  const { t } = useTranslation();
  const size = formatSize(game.size_bytes);
  const rating = game.deck_rating ?? "unknown";
  return (
    <Focusable
      flow-children="column"
      onActivate={() => {}}
      style={{ display: "flex", flexDirection: "column", gap: 4 }}
    >
      {game.install_path && mode === "full" && (
        <Row label={t("info.installPath")} value={game.install_path} />
      )}
      {size && <Row label={t("info.size")} value={size} />}
      {game.executable && mode === "full" && (
        <Row label={t("info.executable")} value={game.executable} />
      )}
      <div style={{ display: "flex", gap: 8, fontSize: 13,
                    alignItems: "center" }}>
        <span style={{ color: "#94a3b8", minWidth: 110 }}>
          {t("info.deckRating")}
        </span>
        <span style={{
          padding: "2px 8px", borderRadius: 4, fontSize: 11,
          background: RATING_COLOR[rating], color: "#0f172a",
        }}>
          {t(`info.rating.${rating}`)}
        </span>
      </div>
    </Focusable>
  );
};
