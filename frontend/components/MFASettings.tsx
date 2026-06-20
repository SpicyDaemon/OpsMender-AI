"use client";

import { useEffect, useState } from "react";
import { KeyRound, ShieldCheck } from "lucide-react";
import { useAuth } from "@/context/auth";
import {
  confirmMFA,
  disableMFA,
  getMFAStatus,
  setupMFA,
  updateOrganizationMFASettings,
} from "@/lib/api";
import type { MFASetupResponse, MFAStatusResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { FormError, Input, Label } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";

export function MFASettings({
  enrollmentOnly = false,
  onEnabled,
}: {
  enrollmentOnly?: boolean;
  onEnabled?: () => void | Promise<void>;
}) {
  const { user } = useAuth();
  const toast = useToast();
  const [status, setStatus] = useState<MFAStatusResponse | null>(null);
  const [setup, setSetup] = useState<MFASetupResponse | null>(null);
  const [code, setCode] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function loadStatus() {
    setStatus(await getMFAStatus());
  }

  useEffect(() => {
    loadStatus().catch((err) =>
      setError(err instanceof Error ? err.message : String(err)),
    );
  }, []);

  async function beginSetup() {
    setBusy(true);
    setError("");
    try {
      setSetup(await setupMFA());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirmSetup() {
    setBusy(true);
    setError("");
    try {
      const result = await confirmMFA(code.trim());
      setRecoveryCodes(result.recovery_codes);
      setSetup(null);
      setCode("");
      await loadStatus();
      toast.success("Multi-factor authentication enabled");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function turnOff() {
    setBusy(true);
    setError("");
    try {
      await disableMFA(
        useRecovery
          ? { recovery_code: disableCode.trim() }
          : { totp_code: disableCode.trim() },
      );
      setDisableCode("");
      await loadStatus();
      toast.success("Multi-factor authentication disabled");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function setRequired(required: boolean) {
    if (!status) return;
    const previous = status.required;
    setStatus({ ...status, required });
    try {
      await updateOrganizationMFASettings(required);
      toast.success(required ? "MFA is now required" : "MFA requirement removed");
    } catch (err) {
      setStatus({ ...status, required: previous });
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm sm:p-6">
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-accent/10 p-2 text-accent">
          <ShieldCheck size={18} />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-fg-primary">
            Multi-factor authentication
          </h2>
          <p className="mt-0.5 text-sm text-fg-secondary">
            Protect local sign-in with a time-based authenticator and one-time
            recovery codes.
          </p>
        </div>
      </div>

      {error && <div className="mt-4"><FormError message={error} /></div>}

      {recoveryCodes.length > 0 && (
        <div className="mt-5 rounded-lg border border-warning/40 bg-warning/10 p-4">
          <p className="text-sm font-semibold text-fg-primary">
            Save these recovery codes now
          </p>
          <p className="mt-1 text-xs text-fg-secondary">
            Each code works once. They will not be shown again.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 font-mono text-sm text-fg-primary">
            {recoveryCodes.map((recoveryCode) => (
              <code key={recoveryCode}>{recoveryCode}</code>
            ))}
          </div>
          {enrollmentOnly && (
            <Button className="mt-4" onClick={() => onEnabled?.()}>
              Continue to dashboard
            </Button>
          )}
        </div>
      )}

      {!status ? (
        <p className="mt-5 text-sm text-fg-muted">Loading security status…</p>
      ) : !status.enabled ? (
        <div className="mt-5">
          {!setup ? (
            <Button onClick={beginSetup} loading={busy}>
              <KeyRound size={14} /> Set up authenticator
            </Button>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={setup.qr_data_url}
                  alt="Authenticator QR code"
                  className="h-44 w-44 rounded-lg border border-border-subtle bg-white p-2"
                />
                <div className="min-w-0 text-sm text-fg-secondary">
                  <p>Scan the QR code with your authenticator app.</p>
                  <p className="mt-2 text-xs">Manual setup key:</p>
                  <code className="mt-1 block break-all rounded bg-bg-muted p-2 font-mono text-xs text-fg-primary">
                    {setup.secret}
                  </code>
                </div>
              </div>
              <div className="max-w-xs">
                <Label htmlFor="mfa-confirm-code">6-digit code</Label>
                <Input
                  id="mfa-confirm-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  placeholder="123456"
                />
              </div>
              <Button
                onClick={confirmSetup}
                loading={busy}
                disabled={code.trim().length < 6}
              >
                Verify and enable
              </Button>
            </div>
          )}
        </div>
      ) : enrollmentOnly && recoveryCodes.length === 0 ? (
        <Button className="mt-5" onClick={() => onEnabled?.()}>
          Continue to dashboard
        </Button>
      ) : !enrollmentOnly ? (
        <div className="mt-5 space-y-4">
          <p className="text-sm text-success">
            Enabled · {status.recovery_codes_remaining} recovery codes remaining
          </p>
          <div className="max-w-sm">
            <Label htmlFor="mfa-disable-code">
              {useRecovery ? "Recovery code" : "Authenticator code"}
            </Label>
            <Input
              id="mfa-disable-code"
              value={disableCode}
              onChange={(event) => setDisableCode(event.target.value)}
              autoComplete="one-time-code"
            />
            <button
              type="button"
              onClick={() => setUseRecovery((value) => !value)}
              className="mt-2 text-xs text-accent hover:underline"
            >
              Use {useRecovery ? "an authenticator code" : "a recovery code"}
            </button>
          </div>
          <Button
            variant="danger"
            onClick={turnOff}
            loading={busy}
            disabled={!disableCode.trim()}
          >
            Disable MFA
          </Button>
        </div>
      ) : null}

      {!enrollmentOnly && user?.role === "admin" && status && (
        <label className="mt-6 flex items-start gap-3 border-t border-border-subtle pt-5">
          <input
            type="checkbox"
            checked={status.required}
            onChange={(event) => setRequired(event.target.checked)}
            className="mt-1"
          />
          <span>
            <span className="block text-sm font-medium text-fg-primary">
              Require MFA for this organization
            </span>
            <span className="block text-xs text-fg-secondary">
              Local-account users are sent to enrollment after password sign-in.
            </span>
          </span>
        </label>
      )}
    </section>
  );
}
