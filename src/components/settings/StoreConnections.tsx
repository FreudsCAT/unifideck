/**
 * StoreConnections — list of registered stores with auth
 * status + connect/disconnect actions.
 *
 * Replaces the legacy hardcoded if/elif of 5 store-specific
 * sections with a single map over `useStores()`. Adding a
 * 6th store requires zero changes to this file — the new
 * store registers itself in the backend `StoreRegistry`
 * and the frontend picks it up automatically.
 */
import React, { FC } from "react";
import {PanelSection, PanelSectionRow, ButtonItem, Field} from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useStores } from "../../contexts/StoreContext";
import { useStoreAuth } from "../../hooks/useStoreAuth";
import { StoreIcon } from "../shared/StoreIcon";
import type { StoreId } from "../../types/api";

const StoreRow: FC<{ storeId: StoreId; displayName: string }> = ({storeId, displayName}) => {
  const { t } = useTranslation();
  const { status, busy, connect, disconnect } = useStoreAuth(storeId);
  const isConnected = status === "connected";
  return (
    <PanelSectionRow>
      <Field
        label={
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <StoreIcon store={storeId} size={16} /> {displayName}
          </span>
        }
        description={t(`auth.status.${status ?? "disconnected"}`)}
      >
        <ButtonItem
          layout="below"
          disabled={busy}
          onClick={() => (isConnected ? disconnect() : connect())}
        >
          {busy
            ? t("common.working")
            : isConnected ? t("auth.disconnect") : t("auth.connect")}
        </ButtonItem>
      </Field>
    </PanelSectionRow>
  );
};

/**
 * Per-store connection list : status badge, Connect /
 * Disconnect button, last-sync timestamp. Driven by the
 * StoreContext + AuthContext combo so the list stays in
 * sync with backend events.
 */
export const StoreConnections: FC = () => {
  const { t } = useTranslation();
  const { stores, loading } = useStores();
  if (loading) return null;
  return (
    <PanelSection title={t("settings.storeConnections")}>
      {stores.map((s) => (
        <StoreRow key={s.name} storeId={s.name}
                  displayName={s.display_name} />
      ))}
    </PanelSection>
  );
};
