/**
 * InstalledGameRow — one installed game in the Downloads tab's
 * "Installed" section.
 *
 * The section this belongs to exists because the tab used to list only
 * downloads *this plugin had performed in its capped history*, so a game
 * installed before the plugin — or eleven downloads ago — was invisible, and
 * the only way to make it appear was to uninstall and reinstall it. The list
 * is now derived from actual install state, so it always reflects the disk.
 *
 * Play and Uninstall both need a Steam appId, which we resolve from the
 * shortcut cache; a game with no shortcut yet (synced but not written) is
 * rendered without actions rather than hidden, so the list still matches the
 * user's library.
 */
import { FC, useMemo, useState } from "react";
import { DialogButton, showModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameActions } from "../../hooks/useGameActions";
import { SteamBridge } from "../../lib/steam-bridge";
import { resolveAppIdFromStoreGame } from "../../lib/library-filters";
import { StoreIcon } from "../shared/StoreIcon";
import { UninstallConfirmModal } from "../modals/UninstallConfirmModal";
import type { Game } from "../../types/api";

interface Props {
  game: Game;
  /** Called after a successful uninstall so the list can re-derive. */
  onUninstalled: () => void;
}

const bridge = new SteamBridge();

const ACTION_BTN_STYLE = {
  fontSize: 11,
  padding: "2px 10px",
  borderRadius: 3,
  fontWeight: 600,
  width: "auto",
  minWidth: 0,
  height: "auto",
  flex: "0 0 auto",
  border: "none",
} as const;

export const InstalledGameRow: FC<Props> = ({ game, onUninstalled }) => {
  const { t } = useTranslation();
  const actions = useGameActions(bridge);
  const [busy, setBusy] = useState(false);

  // The shortcut cache is keyed by STORE_GAME_ID (see `updateUnifideckCache`),
  // which is not always the same string as `Game.id`.
  const appId = useMemo(
    () => resolveAppIdFromStoreGame(game.store, game.store_game_id),
    [game.store, game.store_game_id],
  );

  const confirmUninstall = () => {
    if (appId == null) return;
    showModal(
      <UninstallConfirmModal
        gameId={appId}
        gameTitle={game.title}
        onConfirm={async (deletePrefix: boolean) => {
          setBusy(true);
          try {
            const result = await actions.uninstall(appId, deletePrefix);
            if (result?.success) onUninstalled();
          } finally {
            setBusy(false);
          }
        }}
        closeModal={() => {}}
      />,
    );
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: 6,
        minWidth: 0,
      }}
    >
      {/* flexShrink guard: without it the icon is squeezed by a long
          wrapping title (an svg is shrinkable in a flex row). */}
      <span style={{ display: "inline-flex", flexShrink: 0 }}>
        <StoreIcon store={game.store} size={14} />
      </span>
      {/* Wraps instead of truncating. The QAM panel is narrow and the two
          action buttons take a fixed slice of it, so `nowrap` + ellipsis cut
          most titles to a few characters ("Alex Kidd in …", "Beyond Goo…") —
          useless for telling two games apart. Letting the title use as many
          lines as it needs costs a little height and keeps every name
          readable; `break-word` handles a single token longer than the
          column. */}
      <span
        style={{
          flex: 1,
          fontWeight: 500,
          minWidth: 0,
          overflowWrap: "anywhere",
          wordBreak: "break-word",
          lineHeight: 1.3,
        }}
      >
        {game.title}
      </span>
      {appId != null && (
        // A PLAIN div, not a Focusable: the whole Installed list is wrapped in
        // one `flow-children="grid"` container (see DownloadsTab), and a
        // per-row nav node would break that grid into isolated rows. Plain
        // divs are transparent to the nav tree, so every Play/Uninstall button
        // becomes a direct child of the one grid — which is what makes DOWN
        // from Uninstall land on the next Uninstall instead of jumping column.
        <div style={{ display: "flex", gap: 6, flex: "0 0 auto" }}>
          <DialogButton
            className="unifideck-download-play-btn"
            style={{
              ...ACTION_BTN_STYLE,
              background: "#22c55e",
              color: "#0f172a",
            }}
            disabled={busy || actions.isWorking}
            onClick={() => actions.launch(appId)}
          >
            {t("downloads.play")}
          </DialogButton>
          <DialogButton
            className="unifideck-download-uninstall-btn"
            style={{ ...ACTION_BTN_STYLE }}
            disabled={busy || actions.isWorking}
            onClick={confirmUninstall}
          >
            {t("downloads.uninstall")}
          </DialogButton>
        </div>
      )}
    </div>
  );
};
