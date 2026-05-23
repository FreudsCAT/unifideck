/**
 * GameInfoMetadata — install path, executable, deck rating pill.
 *
 * Install-state rows only — display-side metadata (developer,
 * publisher, release date, Metacritic) lives in
 * {@link GameInfoInfoRow}, and size moved there too to match the
 * staging panel's row layout.
 */
import { FC } from "react";
import { Focusable } from "@decky/ui";
import { useTranslation } from "react-i18next";
import type { Game, DeckRating } from "../../types/api";

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

const Row: FC<{ label: string; value: string }> = ({ label, value }) => (
  <div style={{ display: "flex", gap: 8, fontSize: 13 }}>
    <span style={{ color: "#94a3b8", minWidth: 110 }}>{label}</span>
    <span style={{ flex: 1, wordBreak: "break-all" }}>{value}</span>
  </div>
);

export const GameInfoMetadata: FC<Props> = ({ game, mode }) => {
  const { t } = useTranslation();
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
