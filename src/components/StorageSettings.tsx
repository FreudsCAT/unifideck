/**
 * Settings Tab Component
 *
 * Contains storage location configuration with custom path support.
 * Includes a custom file browser with device quick-access, directory
 * navigation, folder creation, and path selection.
 */

import { FC, useState, useEffect, useCallback, useRef } from "react";
import { call, toaster } from "@decky/api";
import {
  PanelSection,
  PanelSectionRow,
  Field,
  Dropdown,
  DropdownOption,
  DialogButton,
  ButtonItem,
  showModal,
  ModalRoot,
  TextField,
  Toggle,
  Focusable,
} from "@decky/ui";
import { FaFolder, FaTimes } from "react-icons/fa";

import type {
  StorageLocationInfo,
  StorageLocationsResponse,
} from "../types/downloads";

import { t } from "../i18n";

interface BrowseableDevice {
  id: string;
  label: string;
  path: string;
  free_space_gb?: number;
}

/**
 * Storage Location Settings Component
 */
export const StorageSettings: FC = () => {
  const [locations, setLocations] = useState<StorageLocationInfo[]>([]);
  const [defaultStorage, setDefaultStorage] = useState<string>("internal");
  const [saving, setSaving] = useState(false);

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

  useEffect(() => {
    fetchLocations();
  }, [fetchLocations]);

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

  // Open the custom file browser and set result as custom install path
  const handleBrowse = async () => {
    let devices: BrowseableDevice[] = [];
    try {
      const result = await call<
        [],
        { success: boolean; devices: BrowseableDevice[] }
      >("get_browseable_devices");
      if (result.success) {
        devices = result.devices;
      }
    } catch {
      // proceed with empty devices
    }

    showModal(
      <CustomFileBrowser
        devices={devices}
        onSelect={async (selectedPath) => {
          setSaving(true);
          const setResult = await call<
            [string],
            { success: boolean; error?: string; free_space_gb?: number }
          >("set_custom_install_path", selectedPath);

          if (setResult.success) {
            toaster.toast({
              title: t("storageSettings.customPathSet"),
              body: selectedPath,
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
          setSaving(false);
        }}
      />,
    );
  };

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

      <PanelSectionRow>
        <ButtonItem layout="below" onClick={handleBrowse} disabled={saving}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              justifyContent: "center",
            }}
          >
            <FaFolder />
            {t("storageSettings.browseButton")}
          </div>
        </ButtonItem>
      </PanelSectionRow>
      {hasCustomPath && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={handleClearCustom}
            disabled={saving}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                justifyContent: "center",
              }}
            >
              <FaTimes />
              {t("storageSettings.clearCustom")}
            </div>
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};

const FolderIcon: FC = () => (
  <svg
    viewBox="0 0 24 24"
    fill="currentColor"
    width="1em"
    height="1em"
    style={{ flexShrink: 0, opacity: 0.7 }}
  >
    <path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
  </svg>
);

const UpArrowIcon: FC = () => (
  <svg viewBox="0 0 24 24" fill="currentColor" width="1em" height="1em">
    <path d="M4 12l1.41 1.41L11 7.83V20h2V7.83l5.58 5.59L20 12l-8-8-8 8z" />
  </svg>
);

const SORT_OPTIONS: DropdownOption[] = [
  { data: "name", label: "A-Z" },
  { data: "name_desc", label: "Z-A" },
  { data: "modified_newest", label: "Modified (Newest)" },
  { data: "modified_oldest", label: "Modified (Oldest)" },
  { data: "created_newest", label: "Created (Newest)" },
  { data: "created_oldest", label: "Created (Oldest)" },
  { data: "size_largest", label: "Size (Largest)" },
  { data: "size_smallest", label: "Size (Smallest)" },
];

/**
 * Custom file browser modal with device dropdown, directory listing,
 * sorting, hidden file toggle, folder creation, and path selection.
 */
const CustomFileBrowser: FC<{
  devices: BrowseableDevice[];
  onSelect: (path: string) => void;
  closeModal?: () => void;
}> = ({ devices, onSelect, closeModal }) => {
  const [currentPath, setCurrentPath] = useState(
    devices.length > 0 ? devices[0].path : "/",
  );
  const [directories, setDirectories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [sortBy, setSortBy] = useState<string>("name");

  const showHiddenRef = useRef(showHidden);
  const sortByRef = useRef(sortBy);
  showHiddenRef.current = showHidden;
  sortByRef.current = sortBy;

  const fetchDirectory = useCallback(async (path: string) => {
    setLoading(true);
    try {
      const result = await call<
        [string, boolean, string],
        { success: boolean; path: string; directories: string[] }
      >("list_directory", path, showHiddenRef.current, sortByRef.current);
      if (result.success) {
        setCurrentPath(result.path);
        setDirectories(result.directories);
      }
    } catch (e) {
      console.error("[FileBrowser] Error listing directory:", e);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchDirectory(currentPath);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchDirectory(currentPath);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showHidden, sortBy]);

  const navigateTo = (dir: string) => {
    const next =
      currentPath === "/" ? `/${dir}` : `${currentPath}/${dir}`;
    fetchDirectory(next);
  };

  const navigateUp = () => {
    if (currentPath === "/") return;
    const parent =
      currentPath.substring(0, currentPath.lastIndexOf("/")) || "/";
    fetchDirectory(parent);
  };

  const handleCreateFolder = async () => {
    const name = newFolderName.trim();
    if (!name) return;
    const fullPath = `${currentPath}/${name}`;
    const result = await call<
      [string],
      { success: boolean; error?: string; path?: string }
    >("create_directory", fullPath);

    if (result.success) {
      setNewFolderName("");
      setCreatingFolder(false);
      await fetchDirectory(fullPath);
    } else {
      toaster.toast({
        title: t("storageSettings.toastFailedTitle"),
        body: t(result.error || "Unknown error"),
        duration: 5000,
        critical: true,
      });
    }
  };

  const handleSelect = () => {
    closeModal?.();
    onSelect(currentPath);
  };

  // Device dropdown
  const deviceOptions: DropdownOption[] = devices.map((d) => ({
    data: d.path,
    label:
      d.free_space_gb != null
        ? `${d.label} (${d.free_space_gb} GB free)`
        : d.label,
  }));

  const currentDevicePath = devices.find(
    (d) => currentPath === d.path || currentPath.startsWith(d.path + "/"),
  )?.path;

  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <div
        style={{
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          gap: "10px",
          minWidth: "320px",
        }}
      >
        {/* Header: device dropdown + up arrow */}
        <Focusable
          style={{
            display: "flex",
            gap: "8px",
            alignItems: "center",
          }}
        >
          {devices.length > 0 && (
            <div style={{ flex: 1, minWidth: 0 }}>
              <Dropdown
                rgOptions={deviceOptions}
                selectedOption={currentDevicePath}
                onChange={(opt) => fetchDirectory(opt.data as string)}
                strDefaultLabel={t("storageSettings.selectDevice")}
              />
            </div>
          )}
          <DialogButton
            onClick={navigateUp}
            disabled={currentPath === "/"}
            style={{
              minWidth: "48px",
              width: "48px",
              height: "40px",
              padding: "0",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <UpArrowIcon />
          </DialogButton>
        </Focusable>

        {/* Current path */}
        <div
          style={{
            fontSize: "12px",
            color: "#b0b0b0",
            wordBreak: "break-all",
            padding: "4px 0",
          }}
        >
          {currentPath}
        </div>

        {/* Toolbar: show hidden toggle + sort dropdown */}
        <Focusable
          style={{
            display: "flex",
            gap: "8px",
            alignItems: "center",
          }}
        >
          <Focusable
            onActivate={() => setShowHidden(!showHidden)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              padding: "6px 12px",
              cursor: "pointer",
            }}
            onClick={() => setShowHidden(!showHidden)}
          >
            <Toggle
              value={showHidden}
              onChange={(checked) => setShowHidden(checked)}
            />
            <span style={{ fontSize: "12px", whiteSpace: "nowrap" }}>
              {t("storageSettings.showHidden")}
            </span>
          </Focusable>
          <div style={{ minWidth: "140px", flexShrink: 0 }}>
            <Dropdown
              rgOptions={SORT_OPTIONS}
              selectedOption={sortBy}
              onChange={(opt) => setSortBy(opt.data as string)}
            />
          </div>
        </Focusable>

        {/* Directory listing */}
        <Focusable
          style={{
            maxHeight: "280px",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "2px",
          }}
        >
          {loading && directories.length === 0 && (
            <div style={{ color: "#666", fontSize: "12px", padding: "8px" }}>
              {t("storageSettings.loading")}
            </div>
          )}
          {!loading && directories.length === 0 && (
            <div style={{ color: "#666", fontSize: "12px", padding: "8px" }}>
              {t("storageSettings.emptyDirectory")}
            </div>
          )}
          {directories.map((dir) => (
            <DialogButton
              key={dir}
              onClick={() => navigateTo(dir)}
              style={{
                width: "100%",
                textAlign: "left",
                fontSize: "13px",
                padding: "8px 12px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                }}
              >
                <FolderIcon />
                <span>{dir}</span>
              </div>
            </DialogButton>
          ))}
        </Focusable>

        {/* New folder inline input */}
        {creatingFolder && (
          <Focusable
            style={{ display: "flex", gap: "6px", alignItems: "center" }}
          >
            <div style={{ flex: 1 }}>
              <TextField
                value={newFolderName}
                onChange={(e) => setNewFolderName(e.currentTarget.value)}
                focusOnMount={true}
              />
            </div>
            <DialogButton
              onClick={handleCreateFolder}
              disabled={!newFolderName.trim()}
              style={{
                width: "48px",
                minWidth: "48px",
                height: "40px",
                padding: "0",
                fontSize: "12px",
              }}
            >
              {t("storageSettings.newFolderCreate")}
            </DialogButton>
            <DialogButton
              onClick={() => {
                setCreatingFolder(false);
                setNewFolderName("");
              }}
              style={{
                width: "48px",
                minWidth: "48px",
                height: "40px",
                padding: "0",
                fontSize: "12px",
              }}
            >
              {t("confirmModals.cancelTitle")
                ? t("confirmModals.no")
                : "Cancel"}
            </DialogButton>
          </Focusable>
        )}

        {/* Action buttons */}
        <Focusable style={{ display: "flex", gap: "8px" }}>
          <DialogButton
            onClick={handleSelect}
            style={{ flex: 1, fontSize: "13px" }}
          >
            {t("storageSettings.selectFolder")}
          </DialogButton>
          {!creatingFolder && (
            <DialogButton
              onClick={() => setCreatingFolder(true)}
              style={{ fontSize: "13px" }}
            >
              {t("storageSettings.newFolder")}
            </DialogButton>
          )}
        </Focusable>
      </div>
    </ModalRoot>
  );
};

export default StorageSettings;
