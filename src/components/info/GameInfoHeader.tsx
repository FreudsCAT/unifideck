/**
 * GameInfoHeader — top strip with cover, title, store badge.
 *
 * Pure presentational. Receives a `Game` and the current
 * view mode. In compact mode the cover is small (96x144)
 * and the title is single-line ; in full mode the cover is
 * larger (180x270) and the title can wrap.
 *
 * The `<StoreIcon>` from shared/ provides the brand badge.
 */
import { FC } from "react";
import { DialogButton, Focusable } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useViewMode } from "../../hooks/useViewMode";
import { StoreIcon } from "../shared/StoreIcon";
import type { Game } from "../../types/api";

/** Props. */
interface Props {
  game: Game;
  mode: "compact" | "full";
}

/**
 * Header strip of the game-info panel : title, store
 * badge, hero artwork. Hides the Steam fallback header
 * via SteamBridge so we don't show two of them stacked.
 */
export const GameInfoHeader: FC<Props> = ({ game, mode }) => {
  const { t } = useTranslation();
  const { toggle } = useViewMode();
  const coverSize = mode === "compact"
    ? { w: 96, h: 144 }
    : { w: 180, h: 270 };
  return (
    <Focusable
      flow-children="row"
      onActivate={() => {}}
      style={{ display: "flex", gap: 12, alignItems: "flex-start" }}
    >
      {game.cover_image && (
        <img
          src={game.cover_image}
          alt={game.title}
          style={{
            width: coverSize.w,
            height: coverSize.h,
            borderRadius: 4,
            objectFit: "cover",
          }}
        />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <h2 style={{
          margin: 0,
          fontSize: mode === "compact" ? 16 : 22,
          whiteSpace: mode === "compact" ? "nowrap" : "normal",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}>
          {game.title}
        </h2>
        <div style={{ marginTop: 6, display: "flex", alignItems: "center",
                      gap: 6 }}>
          <StoreIcon store={game.store} size={16} />
          <span style={{ fontSize: 12, color: "#94a3b8" }}>
            {game.store}
          </span>
        </div>
        <DialogButton
          onClick={toggle}
          style={{ marginTop: 8, alignSelf: "flex-start", fontSize: 12 }}
        >
          {mode === "compact"
            ? t("info.expandDetails")
            : t("info.collapseDetails")}
        </DialogButton>
      </div>
    </Focusable>
  );
};
