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
import { ModalRoot, DialogButton } from "@decky/ui";
import { call } from "@decky/api";
import { useTranslation } from "react-i18next";

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
  const [showPassword, setShowPassword] = useState(false);

  const handleSubmit = () => {
    if (!email.trim() || !password.trim()) {
      return;
    }
    onSubmit(email.trim(), password.trim());
  };

  return (
    <>
      <style>{`
        .ubisoft-password-wrapper {
          position: relative;
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .ubisoft-password-input {
          flex: 1;
          padding: 8px 12px;
          background-color: rgba(0, 0, 0, 0.3);
          border: 1px solid rgba(255, 255, 255, 0.2);
          border-radius: 4px;
          color: #fff;
          font-size: 14px;
          font-family: inherit;
        }
        .ubisoft-password-input::placeholder {
          color: rgba(255, 255, 255, 0.4);
        }
        .ubisoft-password-input:focus {
          outline: 2px solid #1a9fff;
          outline-offset: -1px;
          background-color: rgba(0, 0, 0, 0.4);
        }
        .ubisoft-toggle-btn {
          padding: 4px 8px;
          background-color: transparent;
          border: 1px solid rgba(255, 255, 255, 0.2);
          border-radius: 4px;
          color: #888;
          cursor: pointer;
          font-size: 11px;
          white-space: nowrap;
        }
        .ubisoft-toggle-btn:hover {
          color: #fff;
          border-color: rgba(255, 255, 255, 0.4);
        }
      `}</style>
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
        </div>

        {/* Form */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "12px",
          }}
        >
          {/* Email */}
          <div>
            <label
              style={{
                display: "block",
                marginBottom: "4px",
                fontSize: "12px",
                color: "#aaa",
              }}
            >
              {t("ubisoftAuth.emailLabel")}
            </label>
            <input
              type="text"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@example.com"
              disabled={isLoading}
              style={{
                width: "100%",
                padding: "8px 12px",
                backgroundColor: "rgba(0, 0, 0, 0.3)",
                border: "1px solid rgba(255, 255, 255, 0.2)",
                borderRadius: "4px",
                color: "#fff",
                fontSize: "14px",
                fontFamily: "inherit",
                boxSizing: "border-box",
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !isLoading) handleSubmit();
              }}
            />
          </div>

          {/* Password */}
          <div>
            <label
              style={{
                display: "block",
                marginBottom: "4px",
                fontSize: "12px",
                color: "#aaa",
              }}
            >
              {t("ubisoftAuth.passwordLabel")}
            </label>
            <div className="ubisoft-password-wrapper">
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                disabled={isLoading}
                className="ubisoft-password-input"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !isLoading) handleSubmit();
                }}
              />
              <button
                type="button"
                className="ubisoft-toggle-btn"
                onClick={() => setShowPassword((s) => !s)}
                disabled={isLoading}
              >
                {showPassword
                  ? t("ubisoftAuth.hidePassword")
                  : t("ubisoftAuth.showPassword")}
              </button>
            </div>
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
            disabled={isLoading || !email.trim() || !password.trim()}
            style={{ minWidth: "140px" }}
          >
            {isLoading
              ? t("ubisoftAuth.signingIn")
              : t("ubisoftAuth.signIn")}
          </DialogButton>
        </div>
      </div>
    </>
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
          <label
            style={{
              display: "block",
              marginBottom: "4px",
              fontSize: "12px",
              color: "#aaa",
            }}
          >
            {t("ubisoftAuth.twoFactorLabel")}
          </label>
          <input
            type="text"
            value={code}
            onChange={(e) =>
              setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
            }
            placeholder="000000"
            disabled={isLoading}
            maxLength={6}
            style={{
              width: "100%",
              padding: "8px 12px",
              backgroundColor: "rgba(0, 0, 0, 0.3)",
              border: "1px solid rgba(255, 255, 255, 0.2)",
              borderRadius: "4px",
              color: "#fff",
              fontSize: "14px",
              fontFamily: "monospace",
              letterSpacing: "4px",
              textAlign: "center",
              boxSizing: "border-box",
            }}
            autoFocus
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
          closeModal?.();
          onAuthComplete();
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
      const result = await call<[string], { success: boolean; error?: string }>(
        "complete_ubisoft_2fa",
        code,
      );

      if (result.success) {
        // Auth fully complete
        closeModal?.();
        onAuthComplete();
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
