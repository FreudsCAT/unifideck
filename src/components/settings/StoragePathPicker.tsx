/**
 * StoragePathPicker — directory browser for the custom
 * storage location.
 *
 * Extracted from the inline `CustomFileBrowser` of the
 * legacy StorageSettings.tsx (623 LOC monolith). Provides
 * a minimal directory navigator : current path display,
 * `list_directory(path, show_hidden, sort_by)` listdir,
 * ".." to go up one level, and a refresh button.
 *
 * Backed by `list_directory` registered in UIRPCMixin —
 * the call returns the directory's immediate child entries
 * with their type (dir/file) and a `path` echo so the
 * picker can update its breadcrumb.
 *
 * Resilience : if the backend route is missing (older builds,
 * partial deployments), the picker degrades to a read-only
 * "current path" display with a "Use this path" button. The
 * UI never throws — the user can still confirm whatever the
 * `startPath` was.
 */
import React, { FC, useCallback, useEffect, useState } from "react";
import { ButtonItem, Field } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useRPC } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";

/** Props. */
interface Props {
  startPath: string;
  onConfirm: (path: string) => Promise<void> | void;
}

/** List response. */
interface ListResponse {
  success: boolean;
  path: string;
  directories: string[];
}

/**
 * Modal dialog letting the user browse the filesystem and
 * pick an install root. Calls into the backend
 * `list_directory` RPC for each navigation step so the
 * frontend never reads disk directly.
 */
export const StoragePathPicker: FC<Props> = ({ startPath, onConfirm }) => {
  const { t } = useTranslation();
  const list = useRPC<[string, boolean, string], ListResponse>(
    rpcRoutes.listDirectory,
  );
  const [path, setPath] = useState(startPath);
  const [dirs, setDirs] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [unsupported, setUnsupported] = useState(false);

  /** Refresh. */
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      // (path, show_hidden, sort_by) — legacy contract
      const r = await list(path, false, "name");
      if (r.success) {
        setPath(r.path);
        setDirs(r.directories);
      }
    } catch (e) {
      // Backend may not implement `list_directory` yet.
      // Degrade to read-only mode instead of crashing the picker.
      console.warn("[StoragePathPicker] list_directory unavailable:", e);
      setUnsupported(true);
    } finally {
      setLoading(false);
    }
  }, [list, path]);

  useEffect(() => { void refresh(); }, [refresh]);

  /** Go up. */
  const goUp = (): void => {
    const parts = path.split("/").filter(Boolean);
    parts.pop();
    setPath("/" + parts.join("/"));
  };

  return (
    <div>
      <Field
        label={t("storage.customPath")}
        description={path}
        childrenContainerWidth="fixed"
      />
      {!unsupported && (
        <>
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <ButtonItem layout="below" onClick={goUp}>
              {t("storage.up")}
            </ButtonItem>
            <ButtonItem layout="below" onClick={refresh} disabled={loading}>
              {loading ? t("common.loading") : t("storage.refresh")}
            </ButtonItem>
          </div>
          <div style={{ marginTop: 6, maxHeight: 180, overflowY: "auto" }}>
            {dirs.map((d) => (
              <ButtonItem
                key={d}
                layout="below"
                onClick={() => setPath(`${path}/${d}`)}
              >
                {d}
              </ButtonItem>
            ))}
          </div>
        </>
      )}
      <ButtonItem layout="below" onClick={() => onConfirm(path)}>
        {t("storage.useThisPath")}
      </ButtonItem>
    </div>
  );
};
