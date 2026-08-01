/**
 * CompatdataCleanupSection — reclaim disk from stale Steam `compatdata`
 * prefixes.
 *
 * Before the compatdata bridge existed, launching a Unifideck shortcut with a
 * compat tool assigned made Steam build a full Proton prefix the game never
 * reads (the launcher points WINEPREFIX at our own per-game directory). They
 * were never pruned on uninstall, so they pile up — hundreds of MB each.
 *
 * Scan-then-confirm: the button first reports how much is reclaimable, and
 * only a second press deletes. Prefixes belonging to the user's *own*
 * non-Steam shortcuts are returned by the scan with `deletable: false` and
 * are never sent for deletion — and the backend re-verifies that regardless
 * of what this component sends.
 */
import { FC, useState } from "react";
import { PanelSection, PanelSectionRow, ButtonItem } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useRPCMutation } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import { useToast } from "../../hooks/useToast";

interface ScanEntry {
  app_id: number;
  name: string;
  classification: "unifideck" | "orphan" | "user";
  size_bytes: number;
  deletable: boolean;
}

interface ScanResult {
  entries: ScanEntry[];
  deletable_count: number;
  deletable_bytes: number;
}

interface DeleteResult {
  deleted_count: number;
  freed_bytes: number;
  refused_count: number;
}

const gb = (bytes: number): string => `${(bytes / 1024 ** 3).toFixed(1)} GB`;

export const CompatdataCleanupSection: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const [pending, setPending] = useState<ScanResult | null>(null);

  const scan = useRPCMutation<[], ScanResult>(rpcRoutes.scanStaleCompatdata);
  const remove = useRPCMutation<[number[]], DeleteResult>(
    rpcRoutes.deleteStaleCompatdata,
  );
  const busy = scan.loading || remove.loading;

  const handleScan = async () => {
    const result = await scan.mutate();
    if (!result) {
      toast.error(
        t("toasts.compatdataScanFailed"),
        scan.error?.message ?? t("errors.unknown"),
      );
      return;
    }
    if (result.deletable_count === 0) {
      toast.info(t("toasts.compatdataNoop"), t("toasts.compatdataNoopMessage"));
      return;
    }
    setPending(result);
  };

  const handleDelete = async () => {
    if (!pending) return;
    const ids = pending.entries.filter((e) => e.deletable).map((e) => e.app_id);
    const result = await remove.mutate(ids);
    setPending(null);
    if (!result) {
      toast.error(
        t("toasts.compatdataDeleteFailed"),
        remove.error?.message ?? t("errors.unknown"),
      );
      return;
    }
    toast.success(
      t("toasts.compatdataCleaned"),
      t("toasts.compatdataCleanedMessage", {
        count: result.deleted_count,
        size: gb(result.freed_bytes),
      }),
    );
  };

  return (
    <PanelSection title={t("compatdata.title")}>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={pending ? handleDelete : handleScan}
          disabled={busy}
        >
          {busy
            ? t("compatdata.working")
            : pending
            ? t("compatdata.confirmDelete", {
                count: pending.deletable_count,
                size: gb(pending.deletable_bytes),
              })
            : t("compatdata.scan")}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
};
