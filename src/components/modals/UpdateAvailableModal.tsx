/**
 * UpdateAvailableModal — shown when the user launches a game that the
 * background sweep has flagged as out of date.
 *
 * Deliberately a choice, not a block: on a handheld, turning "press
 * Play" into a mandatory 40 GB download is hostile, and most single-
 * player titles run fine a build behind. But an online game will simply
 * refuse to connect, and before this the user had no way to know that
 * was why — nothing on the launch path had ever consulted update state.
 *
 * Costs nothing to show: it reads the already-cached sweep result, so
 * there is no network call and no delay added to a launch that has no
 * update pending (in that case this never renders at all).
 */
import { FC } from "react";
import { ConfirmModal, DialogButton } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaPlay, FaSyncAlt } from "react-icons/fa";

interface Props {
  gameTitle: string;
  /** Queue the update and don't launch. */
  onUpdate: () => Promise<void> | void;
  /** Launch anyway, leaving the game on its current build. */
  onPlayAnyway: () => void;
  closeModal: () => void;
}

export const UpdateAvailableModal: FC<Props> = ({
  gameTitle,
  onUpdate,
  onPlayAnyway,
  closeModal,
}) => {
  const { t } = useTranslation();
  return (
    <>
      <style>{`
        .unifideck-update-modal + div { display: none !important; }
        .DialogFooter { display: none !important; }
      `}</style>
      <ConfirmModal
        strTitle={t("updateModal.title")}
        strDescription=""
        bHideCloseIcon={false}
        onOK={closeModal}
        onCancel={closeModal}
      >
        <div className="unifideck-update-modal" style={{ padding: "10px 0" }}>
          <div
            style={{
              marginBottom: 16,
              color: "#ccc",
              fontSize: 14,
              lineHeight: 1.5,
            }}
          >
            {t("updateModal.description", { title: gameTitle })}
          </div>
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
            <DialogButton
              onClick={() => {
                closeModal();
                onPlayAnyway();
              }}
              style={{
                minWidth: 120,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
              }}
            >
              <FaPlay /> {t("updateModal.playAnyway")}
            </DialogButton>
            <DialogButton
              onClick={async () => {
                closeModal();
                await onUpdate();
              }}
              style={{
                minWidth: 120,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
              }}
            >
              <FaSyncAlt /> {t("updateModal.updateNow")}
            </DialogButton>
          </div>
        </div>
      </ConfirmModal>
    </>
  );
};
