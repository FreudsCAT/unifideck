/**
 * GameDetailsViewModeToggle — settings-tab section exposing the
 * compact / full game-details preference. Reads/writes via
 * `useViewMode` so every consumer (GameInfoPanel, etc.) stays in sync.
 */
import { FC } from "react";
import { PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useViewMode } from "../../hooks/useViewMode";

export const GameDetailsViewModeToggle: FC = () => {
  const { t } = useTranslation();
  const { mode, setMode } = useViewMode();
  const compact = mode === "compact";
  return (
    <PanelSection title={t("gameDetailsSettings.title")}>
      <PanelSectionRow>
        <ToggleField
          label={
            compact
              ? t("gameDetailsSettings.simple")
              : t("gameDetailsSettings.detailed")
          }
          checked={compact}
          onChange={(c) => setMode(c ? "compact" : "full")}
        />
      </PanelSectionRow>
    </PanelSection>
  );
};
