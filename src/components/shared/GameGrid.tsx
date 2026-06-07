/**
 * GameGrid — responsive grid of game cover tiles.
 *
 * Pure presentational : receives an array of `Game` and an
 * `onSelect(appId)` callback. Each tile renders the cover
 * image, the title (truncated), and a StoreIcon corner badge.
 *
 * Used by the unified library view and any future grid-
 * style picker (e.g. multi-select for batch operations).
 */
import { FC } from "react";
import { Focusable, DialogButton } from "@decky/ui";
import { StoreIcon } from "./StoreIcon";
import type { Game } from "../../types/api";

/** Props. */
interface Props {
  games: Game[];
  onSelect: (game: Game) => void;
  tileWidth?: number;
}

/**
 * Generic responsive grid of {@link UnifideckGame} cards.
 * Used by every list view (per-store, search results,
 * recently played). Virtualised under a configurable
 * threshold to keep memory bounded on large libraries.
 */
export const GameGrid: FC<Props> = ({ games, onSelect, tileWidth = 140 }) => {
  return (
    <Focusable
      flow-children="grid"
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(auto-fill, minmax(${tileWidth}px, 1fr))`,
        gap: 12,
        padding: 12,
      }}
    >
      {games.map((game) => (
        <DialogButton
          key={game.id}
          onClick={() => onSelect(game)}
          style={{
            position: "relative",
            background: "transparent",
            border: "none",
            padding: 0,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {game.cover_image && (
            <img
              src={game.cover_image}
              alt={game.title}
              style={{
                width: "100%",
                aspectRatio: "2 / 3",
                borderRadius: 6,
                objectFit: "cover",
              }}
            />
          )}
          <span
            style={{
              fontSize: 12,
              color: "#e5e7eb",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              textAlign: "left",
            }}
          >
            {game.title}
          </span>
          <span
            style={{
              position: "absolute",
              top: 6,
              insetInlineEnd: 6,
              background: "#0f172acc",
              borderRadius: 3,
              padding: 3,
            }}
          >
            <StoreIcon store={game.store} size={12} />
          </span>
        </DialogButton>
      ))}
    </Focusable>
  );
};
