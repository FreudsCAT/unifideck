/**
 * StorageSettings — top-level container for storage
 * configuration.
 *
 * Replaces the 623L legacy file. This container loads the
 * location list via RPC, exposes a default-storage selector
 * (calling `set_default_storage_location`), and embeds
 * `<StoragePathPicker>` for browsing custom paths via
 * `list_directory`. Both routes are registered in
 * UIRPCMixin (set_default_storage_location, list_directory).
 */
import React, { FC, useCallback, useState } from "react";
import {
  PanelSection, PanelSectionRow, ButtonItem, Field, Spinner,
} from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useRPC, useRPCQuery } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import { useToast } from "../../hooks/useToast";
import { StoragePathPicker } from "./StoragePathPicker";
import type {
  StorageLocationsResponse, StorageLocation,
} from "../../types/downloads";

/**
 * Storage settings panel : where new installs go (eMMC,
 * SD card, custom path), free-space readout, and per-
 * store overrides. Persists through the backend config
 * service so the choice is shared with the launcher.
 */
export const StorageSettings: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const locationsQuery = useRPCQuery<[], StorageLocationsResponse>(
    rpcRoutes.getStorageLocations, [],
  );
  const setDefaultRPC = useRPC<[StorageLocation], { success: boolean }>(
    rpcRoutes.setDefaultStorageLocation,
  );
  const [saving, setSaving] = useState(false);
  const locations = locationsQuery.data?.locations ?? [];
  const defaultStorage = locationsQuery.data?.default ?? "internal";
  const onSetDefault = useCallback(
    async (id: StorageLocation) => {
      setSaving(true);
      try {
        const r = await setDefaultRPC(id);
        if (r.success) {
          await locationsQuery.refetch();
          toast.success(t("storage.defaultUpdated"));
        }
      } finally {
        setSaving(false);
      }
    },
    [setDefaultRPC, locationsQuery, t, toast],
  );
  if (locationsQuery.loading) {
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
              disabled={!loc.available || saving || defaultStorage === loc.id}
              onClick={() => onSetDefault(loc.id)}
            >
              {defaultStorage === loc.id
                ? t("storage.isDefault")
                : t("storage.setDefault")}
            </ButtonItem>
          </Field>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <StoragePathPicker
          startPath={
            locations.find((l) => l.id === "custom")?.path ?? "/home/deck"
          }
        />
      </PanelSectionRow>
    </PanelSection>
  );
};
