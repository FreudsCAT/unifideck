/**
 * Settings Tab Component
 *
 * Contains storage location configuration with custom path support,
 * device quick-access navigation, and folder creation.
 */

import { FC, useState, useEffect, useCallback } from "react";
import { call, toaster, openFilePicker } from "@decky/api";
import { FileSelectionType } from "@decky/api";
import {
  PanelSection,
  PanelSectionRow,
  Field,
  Dropdown,
  DropdownOption,
  DialogButton,
  showModal,
  ConfirmModal,
  TextField,
} from "@decky/ui";

import type {
  StorageLocationInfo,
  StorageLocationsResponse,
} from "../types/downloads";

import { t } from "../i18n";

interface BrowseableDevice {
  id: string;
  label: string;
  path: string;
}

/**
 * Storage Location Settings Component
 */
export const StorageSettings: FC = () => {
  const [locations, setLocations] = useState<StorageLocationInfo[]>([]);
  const [defaultStorage, setDefaultStorage] = useState<string>("internal");
  const [saving, setSaving] = useState(false);
  const [devices, setDevices] = useState<BrowseableDevice[]>([]);

  const fetchLocations = useCallback(async () => {
    try {
      const result = await call<[], StorageLocationsResponse>(
        "get_storage_locations",
      );
      if (result.success) {
        setLocations(result.locations);
        setDefaultStorage(result.default);
      }
    } catch (error) {
      console.error("[StorageSettings] Error fetching locations:", error);
    }
  }, []);

  const fetchDevices = useCallback(async () => {
    try {
      const result = await call<
        [],
        { success: boolean; devices: BrowseableDevice[] }
      >("get_browseable_devices");
      if (result.success) {
        setDevices(result.devices);
      }
    } catch (error) {
      console.error("[StorageSettings] Error fetching devices:", error);
    }
  }, []);

  // Fetch storage locations and devices on mount
  useEffect(() => {
    fetchLocations();
    fetchDevices();
  }, [fetchLocations, fetchDevices]);

  // Handle storage location change
  const handleStorageChange = async (option: DropdownOption) => {
    const newLocation = option.data as string;
    setSaving(true);

    try {
      const result = await call<[string], { success: boolean; error?: string }>(
        "set_default_storage_location",
        newLocation,
      );

      if (result.success) {
        setDefaultStorage(newLocation);
        toaster.toast({
          title: t("storageSettings.toastUpdatedTitle"),
          body: t("storageSettings.toastUpdatedBody", {
            location: option.label,
          }),
          duration: 3000,
        });
      } else {
        toaster.toast({
          title: t("storageSettings.toastFailedTitle"),
          body: t("storageSettings.toastFailedBody", {
            error: t(result.error || "Unknown error"),
          }),
          duration: 5000,
          critical: true,
        });
      }
    } catch (error) {
      console.error("[StorageSettings] Error setting storage location:", error);
    }

    setSaving(false);
  };

  // Browse from a specific device root and set as custom path
  const handleBrowseFrom = async (startPath: string) => {
    try {
      const result = await openFilePicker(
        FileSelectionType.FOLDER,
        startPath,
        false,
        true,
      );

      if (!result?.realpath) return;

      setSaving(true);
      const setResult = await call<
        [string],
        { success: boolean; error?: string; free_space_gb?: number }
      >("set_custom_install_path", result.realpath);

      if (setResult.success) {
        toaster.toast({
          title: t("storageSettings.customPathSet"),
          body: result.realpath,
          duration: 3000,
        });
        await fetchLocations();
      } else {
        toaster.toast({
          title: t("storageSettings.toastFailedTitle"),
          body: t(setResult.error || "Unknown error"),
          duration: 5000,
          critical: true,
        });
      }
    } catch (error) {
      console.error("[StorageSettings] Error browsing for path:", error);
    }
    setSaving(false);
  };

  // Handle clearing custom path
  const handleClearCustom = async () => {
    setSaving(true);
    try {
      const result = await call<[], { success: boolean }>(
        "clear_custom_install_path",
      );
      if (result.success) {
        await fetchLocations();
      }
    } catch (error) {
      console.error("[StorageSettings] Error clearing custom path:", error);
    }
    setSaving(false);
  };

  // Show create folder modal
  const handleNewFolder = () => {
    let folderName = "";
    let selectedDevicePath = devices.length > 0 ? devices[0].path : "/";

    const deviceOptions: DropdownOption[] = devices.map((d) => ({
      data: d.path,
      label: d.label,
    }));

    showModal(
      <ConfirmModal
        strTitle={t("storageSettings.newFolderTitle")}
        strOKButtonText={t("storageSettings.newFolderCreate")}
        strCancelButtonText={t("confirmModals.no")}
        onOK={async () => {
          if (!folderName.trim()) return;
          const fullPath = `${selectedDevicePath}/${folderName.trim()}`;
          const result = await call<
            [string],
            { success: boolean; error?: string; path?: string }
          >("create_directory", fullPath);

          if (result.success) {
            toaster.toast({
              title: t("storageSettings.folderCreated"),
              body: fullPath,
              duration: 3000,
            });
            // Open file picker at the new folder so user can confirm
            handleBrowseFrom(fullPath);
          } else {
            toaster.toast({
              title: t("storageSettings.toastFailedTitle"),
              body: t(result.error || "Unknown error"),
              duration: 5000,
              critical: true,
            });
          }
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "12px",
            padding: "8px 0",
          }}
        >
          {deviceOptions.length > 0 && (
            <div>
              <label style={{ fontSize: "12px", color: "#999", marginBottom: "4px", display: "block" }}>
                {t("storageSettings.newFolderDevice")}
              </label>
              <Dropdown
                rgOptions={deviceOptions}
                selectedOption={selectedDevicePath}
                onChange={(opt: DropdownOption) => {
                  selectedDevicePath = opt.data as string;
                }}
              />
            </div>
          )}
          <div>
            <label style={{ fontSize: "12px", color: "#999", marginBottom: "4px", display: "block" }}>
              {t("storageSettings.newFolderName")}
            </label>
            <TextField
              onChange={(e) => {
                folderName = e.currentTarget.value;
              }}
              focusOnMount={true}
            />
          </div>
          <p style={{ fontSize: "12px", color: "#888" }}>
            {t("storageSettings.newFolderHint")}
          </p>
        </div>
      </ConfirmModal>,
    );
  };

  // Build dropdown options
  const dropdownOptions: DropdownOption[] = locations
    .filter((loc) => loc.available)
    .map((loc) => ({
      data: loc.id,
      label:
        loc.id === "custom"
          ? t("downloadsTab.customLocation", {
              path: loc.path,
              freeSpace: `${loc.free_space_gb}`,
            })
          : t(`${loc.label}`, { freeSpace: `${loc.free_space_gb}` }),
    }));

  const selectedOption = dropdownOptions.find(
    (opt) => opt.data === defaultStorage,
  );

  const hasCustomPath = locations.some((l) => l.id === "custom");

  return (
    <PanelSection title={t("storageSettings.title")}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "4px",
          width: "100%",
        }}
      >
        <label>{t("storageSettings.installLocationLabel")}</label>
        <div
          style={{ display: "flex", flexDirection: "column", width: "100%" }}
        >
          {dropdownOptions.length > 0 ? (
            <Dropdown
              rgOptions={dropdownOptions}
              selectedOption={selectedOption?.data}
              onChange={handleStorageChange}
              disabled={saving}
            />
          ) : (
            <span style={{ color: "#888", fontSize: "12px" }}>
              {t("storageSettings.loading")}
            </span>
          )}
        </div>
        <p style={{ fontSize: "0.85em", color: "#666" }}>
          {t("storageSettings.installLocationDescription")}
        </p>
      </div>

      {/* Device quick-access buttons */}
      {devices.length > 0 && (
        <PanelSectionRow>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "4px",
              width: "100%",
            }}
          >
            <label style={{ fontSize: "12px", color: "#999" }}>
              {t("storageSettings.browseFromDevice")}
            </label>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "6px",
                width: "100%",
              }}
            >
              {devices.map((device) => (
                <DialogButton
                  key={device.id}
                  onClick={() => handleBrowseFrom(device.path)}
                  disabled={saving}
                  style={{ flex: "1 1 auto", minWidth: "120px", fontSize: "12px", padding: "8px" }}
                >
                  {device.label}
                </DialogButton>
              ))}
            </div>
          </div>
        </PanelSectionRow>
      )}

      <PanelSectionRow>
        <div style={{ display: "flex", gap: "8px", width: "100%" }}>
          <DialogButton onClick={handleNewFolder} disabled={saving}>
            {t("storageSettings.newFolder")}
          </DialogButton>
          {hasCustomPath && (
            <DialogButton onClick={handleClearCustom} disabled={saving}>
              {t("storageSettings.clearCustom")}
            </DialogButton>
          )}
        </div>
      </PanelSectionRow>

      {locations.length > 0 && (
        <PanelSectionRow>
          <Field label={t("storageSettings.pathLabel")}>
            <span style={{ color: "#888", fontSize: "12px" }}>
              {locations.find((l) => l.id === defaultStorage)?.path ||
                t("storageSettings.unknown")}
            </span>
          </Field>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};

export default StorageSettings;
