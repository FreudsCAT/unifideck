/**
 * PickStorageModal — install-time storage location selector.
 *
 * Replaces the bare "Are you sure?" confirmation with a
 * Steam-like modal listing every writable device the backend
 * reports, plus a "Choose Another Location..." option that
 * expands an inline `StoragePathPicker`.
 *
 * All storage data is fetched by the caller (`useInstallFlow`)
 * and passed as props — the modal itself makes no RPC queries
 * so it works inside `showModal`'s portal.
 */
import { FC, useCallback, useState } from "react";
import { ConfirmModal, Focusable, showModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { StoragePathPicker } from "../settings/StoragePathPicker";
import type { StorageLocation, StorageLocationInfo } from "../../types/downloads";

/* ---- Props ---- */

interface Props {
  gameTitle: string;
  gameSizeBytes?: number;
  locations: StorageLocationInfo[];
  defaultLocation: StorageLocation;
  setCustomPath: (path: string) => Promise<boolean>;
  onConfirm: (storage: StorageLocation, customPath?: string) => void;
  closeModal?: () => void;
}

/* ---- Helpers ---- */

function formatSize(bytes: number): string {
  const gb = bytes / 1e9;
  return `${gb.toFixed(1)} GB`;
}

/* ---- Component ---- */

export const PickStorageModal: FC<Props> = ({
  gameTitle,
  gameSizeBytes,
  locations,
  defaultLocation,
  setCustomPath,
  onConfirm,
  closeModal,
}) => {
  const { t } = useTranslation();

  const available = locations.filter((l) => l.available);
  const [selectedId, setSelectedId] = useState<StorageLocation>(defaultLocation);
  const [customPath, setCustomPathState] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const handleInstall = useCallback(async () => {
    setSaving(true);
    try {
      if (selectedId === "custom" && customPath) {
        await setCustomPath(customPath);
      }
      closeModal?.();
      onConfirm(selectedId, customPath ?? undefined);
    } finally {
      setSaving(false);
    }
  }, [selectedId, customPath, setCustomPath, closeModal, onConfirm]);

  return (
    <ConfirmModal
      strTitle={t("pickStorage.title")}
      strOKButtonText={saving ? t("common.working") : t("playButton.install")}
      strCancelButtonText={t("common.cancel")}
      bOKDisabled={saving || (selectedId === "custom" && !customPath)}
      onOK={handleInstall}
      onCancel={closeModal}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 320 }}>
        {/* Game info header */}
        <div style={{ marginBottom: 4 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>{gameTitle}</div>
          {gameSizeBytes != null && gameSizeBytes > 0 && (
            <div style={{ fontSize: 12, color: "#8f98a0", marginTop: 2 }}>
              {t("pickStorage.gameSize")}: {formatSize(gameSizeBytes)}
            </div>
          )}
        </div>

        {/* Location options */}
        {available.map((loc) => {
          const selected = selectedId === loc.id;
          return (
            <Focusable
              key={loc.id}
              onActivate={() => setSelectedId(loc.id)}
              onClick={() => setSelectedId(loc.id)}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 10,
                padding: "10px 12px",
                borderRadius: 6,
                cursor: "pointer",
                borderLeft: `4px solid ${selected ? "#1a9fff" : "transparent"}`,
                background: selected ? "rgba(26, 159, 255, 0.1)" : "rgba(255,255,255,0.03)",
                transition: "background 0.15s",
              }}
            >
              <div style={{
                width: 16, height: 16, borderRadius: "50%",
                border: `2px solid ${selected ? "#1a9fff" : "#666"}`,
                background: selected ? "#1a9fff" : "transparent",
                flexShrink: 0, marginTop: 2,
              }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{loc.label}</div>
                <div style={{ fontSize: 11, color: "#8f98a0", wordBreak: "break-all", marginTop: 2 }}>
                  {loc.path}
                </div>
                <div style={{ fontSize: 11, color: "#8f98a0", marginTop: 1 }}>
                  {loc.free_space_gb.toFixed(1)} GB {t("common.free")}
                </div>
              </div>
            </Focusable>
          );
        })}

        {/* Custom location option */}
        <Focusable
          onActivate={() => setSelectedId("custom")}
          onClick={() => setSelectedId("custom")}
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            padding: "10px 12px",
            borderRadius: 6,
            cursor: "pointer",
            borderLeft: `4px solid ${selectedId === "custom" ? "#1a9fff" : "transparent"}`,
            background: selectedId === "custom" ? "rgba(26, 159, 255, 0.1)" : "rgba(255,255,255,0.03)",
          }}
        >
          <div style={{
            width: 16, height: 16, borderRadius: "50%",
            border: `2px solid ${selectedId === "custom" ? "#1a9fff" : "#666"}`,
            background: selectedId === "custom" ? "#1a9fff" : "transparent",
            flexShrink: 0, marginTop: 2,
          }} />
          <div style={{ fontSize: 13, fontWeight: 600 }}>
            {t("pickStorage.customOption")}
          </div>
        </Focusable>

        {/* Inline file picker for custom */}
        {selectedId === "custom" && (
          <div style={{
            padding: "8px 8px 8px 26px",
            background: "rgba(0,0,0,0.15)",
            borderRadius: 6,
          }}>
            <StoragePathPicker
              startPath={locations.find((l) => l.id === "custom")?.path ?? "/home/deck"}
              onConfirm={(p) => setCustomPathState(p)}
            />
          </div>
        )}
      </div>
    </ConfirmModal>
  );
};

/* ---- Promise helper ---- */

export interface PickStorageResult {
  storage: StorageLocation;
  customPath?: string;
}

/**
 * Open the storage picker modal and resolve with the user's
 * choice, or `null` if they cancelled.
 */
export function pickStorageForInstall(
  gameTitle: string,
  gameSizeBytes: number | undefined,
  locations: StorageLocationInfo[],
  defaultLocation: StorageLocation,
  setCustomPath: (path: string) => Promise<boolean>,
): Promise<PickStorageResult | null> {
  return new Promise((resolve) => {
    let confirmed = false;
    const handle = showModal(
      <PickStorageModal
        gameTitle={gameTitle}
        gameSizeBytes={gameSizeBytes}
        locations={locations}
        defaultLocation={defaultLocation}
        setCustomPath={setCustomPath}
        onConfirm={(storage, customPath) => {
          confirmed = true;
          resolve({ storage, customPath });
        }}
        closeModal={() => {
          handle?.Close();
          if (!confirmed) resolve(null);
        }}
      />,
    );
  });
}
