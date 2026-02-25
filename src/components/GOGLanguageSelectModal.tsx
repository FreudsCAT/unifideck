/**
 * GOG Language Selection Modal Component
 *
 * Shows language selection for GOG games with multiple language options.
 * Only shown when a GOG game has 2+ languages available.
 */

import { FC, useState } from "react";
import { ConfirmModal, Dropdown, DropdownOption } from "@decky/ui";
import { useTranslation } from "react-i18next";

// Language code to display name mapping
const LANGUAGE_NAMES: Record<string, string> = {
  "en-US": "English",
  "de-DE": "Deutsch (German)",
  "fr-FR": "Français (French)",
  "es-ES": "Español (Spanish)",
  "it-IT": "Italiano (Italian)",
  "pt-BR": "Português (Brazilian Portuguese)",
  "ru-RU": "Русский (Russian)",
  "pl-PL": "Polski (Polish)",
  "zh-CN": "简体中文 (Simplified Chinese)",
  "zh-Hans": "简体中文 (Simplified Chinese)",
  "zh-TW": "繁體中文 (Traditional Chinese)",
  "ja-JP": "日本語 (Japanese)",
  "ko-KR": "한국어 (Korean)",
  "nl-NL": "Nederlands (Dutch)",
  "tr-TR": "Türkçe (Turkish)",
  "cs-CZ": "Čeština (Czech)",
  "hu-HU": "Magyar (Hungarian)",
  "sv-SE": "Svenska (Swedish)",
  "da-DK": "Dansk (Danish)",
  "fi-FI": "Suomi (Finnish)",
  "no-NO": "Norsk (Norwegian)",
  "uk-UA": "Українська (Ukrainian)",
  "ar-SA": "العربية (Arabic)",
  "th-TH": "ไทย (Thai)",
};

interface GOGLanguageSelectModalProps {
  gameTitle: string;
  languages: string[];
  onConfirm: (language: string) => void;
  closeModal?: () => void;
}

export const GOGLanguageSelectModal: FC<GOGLanguageSelectModalProps> = ({
  gameTitle,
  languages,
  onConfirm,
  closeModal,
}) => {
  const { t } = useTranslation();

  // Guard: ensure languages array is valid
  const safeLanguages = languages && languages.length > 0 ? languages : ["en-US"];

  // Default to first language (usually en-US or the primary language)
  const [selectedLanguage, setSelectedLanguage] = useState<string>(safeLanguages[0]);

  // Build dropdown options
  const dropdownOptions: DropdownOption[] = safeLanguages.map((lang) => ({
    data: lang,
    label: LANGUAGE_NAMES[lang] || lang,
  }));

  const handleLanguageChange = (option: DropdownOption) => {
    setSelectedLanguage(option.data as string);
  };

  const selectedOption = dropdownOptions.find(
    (opt) => opt.data === selectedLanguage
  );

  return (
    <ConfirmModal
      strTitle={t("gogLanguageModal.title", "Select Language")}
      strDescription={t("gogLanguageModal.description", {
        defaultValue: "Choose which language to download for {{title}}",
        title: gameTitle,
      })}
      bHideCloseIcon={false}
      strOKButtonText={t("gogLanguageModal.install", "Install")}
      strCancelButtonText={t("gogLanguageModal.cancel", "Cancel")}
      onOK={() => { closeModal?.(); onConfirm(selectedLanguage); }}
      onCancel={closeModal}
    >
      <div
        style={{
          padding: "12px",
          backgroundColor: "rgba(0, 0, 0, 0.2)",
          borderRadius: "8px",
        }}
      >
        <label
          style={{
            display: "block",
            marginBottom: "8px",
            color: "#fff",
            fontSize: "14px",
          }}
        >
          {t("gogLanguageModal.label", "Language")}
        </label>
        <Dropdown
          rgOptions={dropdownOptions}
          selectedOption={selectedOption?.data}
          onChange={handleLanguageChange}
        />
      </div>
    </ConfirmModal>
  );
};

export default GOGLanguageSelectModal;
