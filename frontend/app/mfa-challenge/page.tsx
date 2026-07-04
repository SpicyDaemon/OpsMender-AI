"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { Button } from "@/components/ui/Button";
import { FormError, Input, Label } from "@/components/ui/Input";
import { setToken, verifyMFA } from "@/lib/api";

export default function MFAChallengePage() {
  const router = useRouter();
  const [challengeToken, setChallengeToken] = useState("");
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const stored = sessionStorage.getItem("opsmender_mfa_token");
    if (!stored) {
      router.replace("/login");
      return;
    }
    setChallengeToken(stored);
  }, [router]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!challengeToken) return;
    setLoading(true);
    setError("");
    try {
      const response = await verifyMFA(
        useRecovery
          ? { mfa_token: challengeToken, recovery_code: code.trim() }
          : { mfa_token: challengeToken, totp_code: code.trim() },
      );
      setToken(response.access_token);
      sessionStorage.removeItem("opsmender_mfa_token");
      window.location.href = "/dashboard";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title="Verify your identity"
      description="Enter a code from your authenticator app to finish signing in."
      eyebrow="Security check"
      footer={null}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <Label htmlFor="mfa-code">
            {useRecovery ? "Recovery code" : "Authenticator code"}
          </Label>
          <Input
            id="mfa-code"
            autoFocus
            required
            inputMode={useRecovery ? "text" : "numeric"}
            autoComplete="one-time-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder={useRecovery ? "ABCD-1234" : "123456"}
          />
        </div>
        <button
          type="button"
          onClick={() => {
            setUseRecovery((value) => !value);
            setCode("");
          }}
          className="text-sm text-accent-text hover:underline"
        >
          Use {useRecovery ? "an authenticator code" : "a recovery code"}
        </button>
        {error && <FormError message={error} />}
        <Button
          type="submit"
          className="w-full justify-center"
          loading={loading}
          disabled={!challengeToken || code.trim().length < 6}
        >
          Verify
        </Button>
      </form>
    </AuthShell>
  );
}
