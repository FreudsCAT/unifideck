/**
 * StorageSettings — top-level container for storage
 * configuration.
 *
 * Replaces the 623L legacy file. This container loads the
 * location list via `useStorageConfig`, exposes a
 * default-storage selector (calling
 * `set_default_storage_location`), and embeds
 * `<StoragePathPicker>` for browsing custom paths via
 * `list_directory`. The custom path picked is persisted via
 * `set_custom_install_path` then promoted to the default
 * storage location.
 *
 * Per PDF rule : all RPC traffic flows through the
 * `useStorageConfig` hook ; this file stays presentational.
 */
import React, { FC } from "react";
import {
  PanelSection, PanelSectionRow, ButtonItem, Field, Spinner, showModal,
} from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useStorageConfig } from "../../hooks/useStorageConfig";
import { useToast } from "../../hooks/useToast";
import { StorageBrowserModal } from "../modals/StorageBrowserModal";
import type { StorageLocation } from "../../types/downloads";

/**
 * Storage settings panel : where new installs go (eMMC,
 * SD card, custom path), free-space readout, and per-
 * store overrides. Persists through the backend config
 * service so the choice is shared with the launcher.
 */
export const StorageSettings: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const {
    locations, defaultLocation, loading, setDefault, setCustomPath,
  } = useStorageConfig();

  const onSetDefault = async (id: StorageLocation): Promise<void> => {
    if (await setDefault(id)) {
      toast.success(t("storage.defaultUpdated"));
    } else {
      toast.error(t("storage.setDefaultFailed"));
    }
  };

  const onConfirmCustom = async (path: string): Promise<void> => {
    if (!(await setCustomPath(path))) {
      toast.error(t("storage.customPathFailed"));
      return;
    }
    if (await setDefault("custom")) {
      toast.success(t("storage.customPathSet", { path }));
    }
  };

  if (loading) {
    return (
      <PanelSection title={t("storage.title")}>
        <PanelSectionRow><Spinner /></PanelSectionRow>
      </PanelSection>
    );
  }
  return (
    <PanelSection title={t("storage.title")}>
      {locations.map((loc) => (
        <PanelSectionRow key={loc.id}>
          <Field
            label={loc.label}
            description={`${loc.path} · ${loc.free_space_gb.toFixed(1)} GB`}
            childrenContainerWidth="fixed"
          >
            <ButtonItem
              layout="below"
              disabled={!loc.available || defaultLocation === loc.id}
              onClick={() => onSetDefault(loc.id)}
            >
              {defaultLocation === loc.id
                ? t("storage.isDefault")
                : t("storage.setDefault")}
            </ButtonItem>
          </Field>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() =>
            showModal(
              <StorageBrowserModal
                startPath={
                  locations.find((l) => l.id === "custom")?.path ?? "/home/deck"
                }
                onConfirm={onConfirmCustom}
                closeModal={() => {}}
              />,
            )
          }
        >
          {t("storageSettings.browseButton")}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
};
