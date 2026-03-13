/**
 * Ubisoft Connect Authentication Modal
 *
 * Two-step auth flow:
 * 1. Credentials form (email + password) → if 2FA required, show 2FA form
 * 2. 2FA form (verification code) → calls onAuthComplete when done
 *
 * Uses a single ModalRoot in the parent to prevent modal destruction on step
 * transitions. Child forms render only their inner content (no ModalRoot).
 */

import { FC, useState } from "react";
import { ModalRoot, DialogButton, TextField } from "@decky/ui";
import { call, toaster } from "@decky/api";
import { useTranslation } from "react-i18next";
import { launchUbisoftAuthViaShortcut } from "../utils/ubisoftShortcutLaunch";

interface UbisoftAuthModalProps {
  onAuthComplete: () => void;
  closeModal?: () => void;
}

type AuthStep = "credentials" | "2fa";

const CredentialsForm: FC<{
  onSubmit: (email: string, password: string) => void;
  onCancel: () => void;
  isLoading: boolean;
  error: string | null;
}> = ({ onSubmit, onCancel, isLoading, error }) => {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const handleSubmit = () => {
    if (!email.trim() || !password.trim()) {
      return;
    }
    onSubmit(email.trim(), password.trim());
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "20px",
        padding: "20px",
        minWidth: "500px",
      }}
    >
      {/* Title */}
      <div>
        <h2 style={{ margin: 0, fontSize: "20px", fontWeight: "bold" }}>
          {t("ubisoftAuth.title")}
        </h2>
        <p
          style={{
            margin: "8px 0 0 0",
            fontSize: "14px",
            color: "#aaa",
          }}
        >
          {t("ubisoftAuth.description")}
        </p>
        <p
          style={{
            margin: "8px 0 0 0",
            fontSize: "13px",
            color: "#ff6b6b",
            fontWeight: 700,
          }}
        >
          You will be required to login twice for Ubisoft
        </p>
      </div>

      {/* Form */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <TextField
          label={t("ubisoftAuth.emailLabel")}
          value={email}
          onChange={(e) => setEmail(e.currentTarget.value)}
          disabled={isLoading}
          focusOnMount={true}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !isLoading) handleSubmit();
          }}
        />

        <TextField
          label={t("ubisoftAuth.passwordLabel")}
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
          disabled={isLoading}
          bIsPassword={true}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !isLoading) handleSubmit();
          }}
        />

        {/* Error */}
        {error && <div style={{ color: "#ff6b6b", fontSize: "12px" }}>{error}</div>}
      </div>

      {/* Action buttons */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: "12px",
        }}
      >
        <DialogButton
          onClick={onCancel}
          disabled={isLoading}
          style={{ minWidth: "140px" }}
        >
          {t("ubisoftAuth.cancel")}
        </DialogButton>
        <DialogButton
          onClick={handleSubmit}
          disabled={isLoading || !email.trim() || !password.trim()}
          style={{ minWidth: "140px" }}
        >
          {isLoading ? t("ubisoftAuth.signingIn") : t("ubisoftAuth.signIn")}
        </DialogButton>
      </div>
    </div>
  );
};

const TwoFactorForm: FC<{
  onSubmit: (code: string) => void;
  onCancel: () => void;
  isLoading: boolean;
  error: string | null;
}> = ({ onSubmit, onCancel, isLoading, error }) => {
  const { t } = useTranslation();
  const [code, setCode] = useState("");

  const handleSubmit = () => {
    if (!code.trim()) {
      return;
    }
    onSubmit(code.trim());
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "20px",
        padding: "20px",
        minWidth: "500px",
      }}
    >
      {/* Title */}
      <div>
        <h2 style={{ margin: 0, fontSize: "20px", fontWeight: "bold" }}>
          {t("ubisoftAuth.twoFactorTitle")}
        </h2>
        <p
          style={{
            margin: "8px 0 0 0",
            fontSize: "14px",
            color: "#aaa",
          }}
        >
          {t("ubisoftAuth.twoFactorDescription")}
        </p>
      </div>

      {/* Form */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        {/* 2FA Code */}
        <div>
          <TextField
            label={t("ubisoftAuth.twoFactorLabel")}
            value={code}
            onChange={(e) =>
              setCode(e.currentTarget.value.replace(/\D/g, "").slice(0, 6))
            }
            disabled={isLoading}
            mustBeNumeric={true}
            focusOnMount={true}
            style={{
              fontFamily: "monospace",
              letterSpacing: "4px",
              textAlign: "center",
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !isLoading && code.length === 6) {
                handleSubmit();
              }
            }}
          />
        </div>

        {/* Error */}
        {error && (
          <div style={{ color: "#ff6b6b", fontSize: "12px" }}>
            {error}
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: "12px",
        }}
      >
        <DialogButton
          onClick={onCancel}
          disabled={isLoading}
          style={{ minWidth: "140px" }}
        >
          {t("ubisoftAuth.cancel")}
        </DialogButton>
        <DialogButton
          onClick={handleSubmit}
          disabled={isLoading || code.length !== 6}
          style={{ minWidth: "140px" }}
        >
          {isLoading ? t("ubisoftAuth.verifying") : t("ubisoftAuth.verify")}
        </DialogButton>
      </div>
    </div>
  );
};

export const UbisoftAuthModal: FC<UbisoftAuthModalProps> = ({
  onAuthComplete,
  closeModal,
}) => {
  const { t } = useTranslation();
  const [step, setStep] = useState<AuthStep>("credentials");
  const [credError, setCredError] = useState<string | null>(null);
  const [twoFAError, setTwoFAError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleCancel = () => {
    if (!isLoading) {
      closeModal?.();
    }
  };

  const finishSuccessfulAuth = async (launchUpcAuth?: boolean) => {
    closeModal?.();

    // Launch auth shortcut FIRST, before onAuthComplete triggers state refresh
    if (launchUpcAuth) {
      let launchResult = await launchUbisoftAuthViaShortcut();
      if (!launchResult.success) {
        launchResult = await call<[], { success: boolean; error?: string }>(
          "connect_ubisoft_account",
        );
      }

      if (!launchResult.success) {
        toaster.toast({
          title: t("toasts.ubisoftLauncherOpenFailed"),
          body:
            launchResult.error ||
            t("toasts.ubisoftLauncherOpenFailedMessage"),
          duration: 10000,
          critical: true,
        });
      }
    }

    onAuthComplete();
  };

  const handleCredentialsSubmit = async (
    email: string,
    password: string,
  ) => {
    setCredError(null);
    setIsLoading(true);

    try {
      const result = await call<
        [string, string],
        {
          success: boolean;
          requires_2fa?: boolean;
          launch_upc_auth?: boolean;
          error?: string;
          message?: string;
        }
      >("start_ubisoft_auth", email, password);

      console.log("[UbisoftAuth] start_ubisoft_auth response:", JSON.stringify(result));

      if (result.success) {
        if (result.requires_2fa) {
          // Move to 2FA step (don't close modal yet)
          setStep("2fa");
        } else {
          // Auth complete without 2FA
          await finishSuccessfulAuth(result.launch_upc_auth);
        }
      } else {
        setCredError(
          result.error || t("ubisoftAuth.errorLoginFailed"),
        );
      }
    } catch (error: any) {
      setCredError(error.message || String(error));
    } finally {
      setIsLoading(false);
    }
  };

  const handleTwoFASubmit = async (code: string) => {
    setTwoFAError(null);
    setIsLoading(true);

    try {
      const result = await call<
        [string],
        { success: boolean; launch_upc_auth?: boolean; error?: string }
      >(
        "complete_ubisoft_2fa",
        code,
      );

      if (result.success) {
        // Auth fully complete
        await finishSuccessfulAuth(result.launch_upc_auth);
      } else {
        setTwoFAError(result.error || t("ubisoftAuth.errorInvalid2FA"));
      }
    } catch (error: any) {
      setTwoFAError(error.message || String(error));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <ModalRoot
      onCancel={handleCancel}
      bDisableBackgroundDismiss={true}
    >
      {step === "2fa" ? (
        <TwoFactorForm
          onSubmit={handleTwoFASubmit}
          onCancel={handleCancel}
          isLoading={isLoading}
          error={twoFAError}
        />
      ) : (
        <CredentialsForm
          onSubmit={handleCredentialsSubmit}
          onCancel={handleCancel}
          isLoading={isLoading}
          error={credError}
        />
      )}
    </ModalRoot>
  );
};
