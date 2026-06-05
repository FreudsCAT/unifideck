/**
 * GameInfoNavButtons — Steam navigation + support buttons.
 *
 * Bottom row of the panel. Five Steam-hosted destinations are
 * gated on ``meta.has_steam_store_page`` because the URLs only
 * resolve for shortcuts that map to a real Steam Store entry —
 * showing them for a generic Epic / GOG / Amazon game would
 * just 404 in the Steam client.
 *
 * "Store Page" and "Support" are always shown: the former
 * falls back to the third-party ``meta.store_url`` and the
 * latter to a store-keyed support URL map.
 */
import { FC } from "react";
import { DialogButton, Focusable } from "@decky/ui";
import { useTranslation } from "react-i18next";
import type { GameMetadata, StoreId } from "../../types/api";

interface Props {
  meta: GameMetadata;
}

const SUPPORT_URLS: Record<string, string> = {
  epic:      "https://www.epicgames.com/help/assistant",
  gog:       "https://support.gog.com/hc/en-us?product=gog",
  amazon:    "https://www.amazon.in/gp/help/customer/display.html?nodeId=GA5ZHN5T2JX8UGF7",
  ubisoft:   "https://www.ubisoft.com/en-us/help",
  ea:        "https://help.ea.com/en/",
  battlenet: "https://us.battle.net/support/en/",
  itch:      "https://itch.io/support",
  microsoft: "https://support.xbox.com/en-US/help/games-apps/game-setup-and-play/troubleshoot-pc-games",
  steam:     "https://help.steampowered.com",
};

function openUrl(url: string): void {
  window.open(url, "_blank", "width=1024,height=768,popup=yes");
}

function getSupportUrl(store: StoreId, steamAppId: number): string {
  if (SUPPORT_URLS[store]) return SUPPORT_URLS[store];
  return steamAppId
    ? `https://help.steampowered.com/en/wizard/HelpWithGame/?appid=${steamAppId}`
    : SUPPORT_URLS.steam;
}

const buttonStyle = {
  padding: "4px 12px",
  fontSize: 12,
  minWidth: 0,
  width: "auto",
  flex: "0 0 auto",
  display: "inline-flex",
  alignItems: "center",
} as const;

export const GameInfoNavButtons: FC<Props> = ({ meta }) => {
  const { t } = useTranslation();
  const steamId = meta.steam_app_id;
  const hasSteam = meta.has_steam_store_page && steamId > 0;
  const storePageUrl = hasSteam
    ? `steam://store/${steamId}`
    : meta.store_url;

  return (
    <Focusable
      flow-children="row"
      onFocus={(e: React.FocusEvent<HTMLDivElement>) => {
        e.currentTarget.scrollIntoView({ behavior: "smooth", block: "center" });
      }}
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 8,
        alignItems: "center",
      }}
    >
      {storePageUrl && (
        <DialogButton
          className="unifideck-nav-button"
          style={buttonStyle}
          onClick={() => openUrl(storePageUrl)}
        >
          {t("gameInfoPanel.buttons.storePage")}
        </DialogButton>
      )}
      {hasSteam && (
        <>
          <DialogButton
            className="unifideck-nav-button"
            style={buttonStyle}
            onClick={() => openUrl(
              `steam://openurl/https://store.steampowered.com/dlc/${steamId}`,
            )}
          >
            {t("gameInfoPanel.buttons.dlc")}
          </DialogButton>
          <DialogButton
            className="unifideck-nav-button"
            style={buttonStyle}
            onClick={() => openUrl(`steam://url/GameHub/${steamId}`)}
          >
            {t("gameInfoPanel.buttons.communityHub")}
          </DialogButton>
          <DialogButton
            className="unifideck-nav-button"
            style={buttonStyle}
            onClick={() => openUrl(
              `steam://openurl/https://store.steampowered.com/points/shop/app/${steamId}`,
            )}
          >
            {t("gameInfoPanel.buttons.pointsShop")}
          </DialogButton>
          <DialogButton
            className="unifideck-nav-button"
            style={buttonStyle}
            onClick={() => openUrl(
              `steam://openurl/https://steamcommunity.com/app/${steamId}/discussions/`,
            )}
          >
            {t("gameInfoPanel.buttons.discussions")}
          </DialogButton>
          <DialogButton
            className="unifideck-nav-button"
            style={buttonStyle}
            onClick={() => openUrl(
              `steam://openurl/https://steamcommunity.com/app/${steamId}/guides/`,
            )}
          >
            {t("gameInfoPanel.buttons.guides")}
          </DialogButton>
        </>
      )}
      <DialogButton
        className="unifideck-nav-button"
        style={buttonStyle}
        onClick={() => openUrl(getSupportUrl(meta.store, steamId))}
      >
        {t("gameInfoPanel.buttons.support")}
      </DialogButton>
    </Focusable>
  );
};
