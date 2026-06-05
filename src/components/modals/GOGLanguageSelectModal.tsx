/**
 * GOGLanguageSelectModal — language picker for multi-language
 * GOG installs.
 *
 * GOG games can ship in multiple languages. When the backend's
 * `get_gog_game_languages` returns more than one option, the
 * install flow defers to this modal so the user picks before
 * the download is queued. With one language available the
 * modal is skipped entirely (queued with the default).
 *
 * Pure presentational : the actual install RPC is the caller's
 * responsibility — this component only collects the choice.
 */
import { FC, useState } from "react";
import { ConfirmModal, Dropdown, DropdownOption } from "@decky/ui";
import { useTranslation } from "react-i18next";

/** Display labels for GOG language codes. Falls back to the
 *  raw code if a code isn't recognised — the modal still works,
 *  just without the localised name. */
const LANGUAGE_NAMES: Record<string, string> = {
  "en-US": "English",
  "de-DE": "Deutsch (German)",
  "fr-FR": "Français (French)",
  "es-ES": "Español (Spanish)",
  "it-IT": "Italiano (Italian)",
  "pt-BR": "Português (Brasil)",
  "ru-RU": "Русский (Russian)",
  "pl-PL": "Polski (Polish)",
  "zh-CN": "简体中文 (Simplified Chinese)",
  "zh-Hans": "简体中文 (Simplified Chinese)",
  "zh-TW": "繁體中文 (Traditional Chinese)",
  "ja-JP": "日本語 (Japanese)",
  "ko-KR": "한국어 (Korean)",
  "nl-NL": "Nederlands (Dutch)",
  "tr-TR": "Türkçe (Turkish)",
  "uk-UA": "Українська (Ukrainian)",
  "cs-CZ": "Čeština (Czech)",
  "hu-HU": "Magyar (Hungarian)",
  "sv-SE": "Svenska (Swedish)",
  "da-DK": "Dansk (Danish)",
  "fi-FI": "Suomi (Finnish)",
  "no-NO": "Norsk (Norwegian)",
  "ar-SA": "العربية (Arabic)",
  "th-TH": "ไทย (Thai)",
};

interface Props {
  gameTitle: string;
  languages: string[];
  onConfirm: (language: string) => void;
  closeModal?: () => void;
}

/**
 * Single-select dropdown of GOG language codes. Confirm =
 * close + invoke `onConfirm(language)` so the parent can
 * call `install_game(..., { language })`.
 */
export const GOGLanguageSelectModal: FC<Props> = ({
  gameTitle, languages, onConfirm, closeModal,
}) => {
  const { t } = useTranslation();
  const safeLanguages = languages.length > 0 ? languages : ["en-US"];
  const [selected, setSelected] = useState<string>(safeLanguages[0]);

  const options: DropdownOption[] = safeLanguages.map((lang) => ({
    data: lang,
    label: LANGUAGE_NAMES[lang] ?? lang,
  }));

  return (
    <ConfirmModal
      strTitle={t("gogLanguageModal.title")}
      strDescription={t("gogLanguageModal.description", { title: gameTitle })}
      strOKButtonText={t("gogLanguageModal.install")}
      strCancelButtonText={t("common.cancel")}
      onOK={() => { onConfirm(selected); closeModal?.(); }}
      onCancel={closeModal}
      bHideCloseIcon={false}
    >
      <div
        style={{
          padding: 12,
          background: "rgba(0, 0, 0, 0.2)",
          borderRadius: 8,
        }}
      >
        <label
          style={{
            display: "block",
            marginBottom: 8,
            color: "#fff",
            fontSize: 14,
          }}
        >
          {t("gogLanguageModal.label")}
        </label>
        <Dropdown
          rgOptions={options}
          selectedOption={selected}
          onChange={(opt: DropdownOption) => setSelected(opt.data as string)}
        />
      </div>
    </ConfirmModal>
  );
};
