/**
 * CollectionsToggle — settings-tab section for the opt-in `[Unifideck]`
 * Steam Collections. Default OFF: most users get the in-library tabs
 * (which sync nowhere), and only those who want store collections mirrored
 * into Desktop mode / their other Steam devices turn this on. Reads/writes
 * via `useCollectionsEnabled`, so toggling takes effect live — turning it
 * off deletes the collections immediately.
 */
import { FC } from "react";
import { PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useCollectionsEnabled } from "../../hooks/useCollectionsEnabled";

export const CollectionsToggle: FC = () => {
  const { t } = useTranslation();
  const { enabled, setEnabled } = useCollectionsEnabled();
  return (
    <PanelSection title={t("collectionSettings.title")}>
      <PanelSectionRow>
        <ToggleField
          label={
            enabled
              ? t("collectionSettings.enabled")
              : t("collectionSettings.disabled")
          }
          checked={enabled}
          onChange={setEnabled}
        />
      </PanelSectionRow>
    </PanelSection>
  );
};
