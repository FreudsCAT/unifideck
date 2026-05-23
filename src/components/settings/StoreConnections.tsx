/**
 * StoreConnections — per-store login status + Sign-in / Logout action.
 *
 * Rebuilt from staging:`src/components/settings/StoreConnections.tsx`:
 * rich rows with a prominent StoreIcon (green when connected, white
 * when disconnected), the localised display name, and the new
 * `StoreAuthButton` as the action target. Driver hooks (`useStores`,
 * `useStoreAuth`) come from the current architecture so the data
 * plane is unchanged.
 */
import { FC, useCallback, useState } from "react";
import { PanelSection, PanelSectionRow, Field } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { call } from "@decky/api";
import { FaSync } from "react-icons/fa";
import { useStores } from "../../contexts/StoreContext";
import { useStoreAuth } from "../../hooks/useStoreAuth";
import { StoreIcon } from "../shared/StoreIcon";
import { StoreAuthButton } from "./StoreAuthButton";
import { rpcRoutes } from "../../api/rpc-routes";
import type { StoreId } from "../../types/api";

interface RowConfig {
  notInstalledStatus?: string;
  notInstalledMessage?: string;
}

const ROW_CONFIG: Partial<Record<StoreId, RowConfig>> = {
  epic: {
    notInstalledStatus: "legendary_not_installed",
    notInstalledMessage: "storeConnections.legendaryNotInstalled",
  },
  amazon: {
    notInstalledStatus: "nile_not_installed",
    notInstalledMessage: "storeConnections.nileNotInstalled",
  },
};

const StoreRow: FC<{ storeId: StoreId; displayName: string }> = ({
  storeId, displayName,
}) => {
  const { t } = useTranslation();
  const { status, busy, connect, disconnect } = useStoreAuth(storeId);
  const isConnected = status === "connected";
  const config = ROW_CONFIG[storeId];
  const showNotInstalled = config?.notInstalledStatus
    && status === config.notInstalledStatus;
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await call<[string], unknown>(rpcRoutes.refreshStore, storeId);
    } catch {
      // RPC failure is silent — the store row isn't a progress
      // display; the main sync bar and log carry the diagnostics.
    } finally {
      setRefreshing(false);
    }
  }, [storeId]);

  return (
    <div>
      <div style={{
        display: "flex", alignItems: "center", flexDirection: "row",
        justifyContent: "space-between", padding: 0,
      }}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <StoreIcon
            store={storeId}
            size="18px"
            color={isConnected ? "#4ade80" : "#fff"}
          />
          <span style={{ fontSize: 14 }}>{displayName}</span>
          {isConnected && (
            <span
              onClick={(e) => { e.stopPropagation(); void onRefresh(); }}
              style={{
                cursor: refreshing ? "default" : "pointer",
                opacity: refreshing ? 0.4 : 0.6,
                marginLeft: 4,
              }}
              title={t("librarySync.refreshStore", "Refresh this store")}
            >
              <FaSync style={{
                fontSize: 11,
                animation: refreshing ? "spin 1s linear infinite" : "none",
              }} />
            </span>
          )}
        </div>
        <StoreAuthButton
          store={storeId}
          status={status ?? "disconnected"}
          busy={busy}
          onConnect={() => void connect()}
          onDisconnect={() => void disconnect()}
        />
      </div>
      {showNotInstalled && config?.notInstalledMessage && (
        <PanelSectionRow>
          <Field description={t(config.notInstalledMessage)} />
        </PanelSectionRow>
      )}
    </div>
  );
};

export const StoreConnections: FC = () => {
  const { t } = useTranslation();
  const { stores, loading } = useStores();
  if (loading) return null;
  return (
    <PanelSection title={t("storeConnections.title")}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {stores.map((s) => (
          <StoreRow
            key={s.name}
            storeId={s.name}
            displayName={s.display_name}
          />
        ))}
      </div>
    </PanelSection>
  );
};
