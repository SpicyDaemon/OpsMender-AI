"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { FormAlert, Input, Label } from "@/components/ui/Input";
import { PasswordField } from "@/components/ui/PasswordField";
import {
  getMe,
  getRegistrationOpen,
  resolveTenant,
  setToken,
} from "@/lib/api";
import type { TenantContextResponse } from "@/lib/types";
import { useDashboardNavigation } from "@/lib/use-dashboard-navigation";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const navigateDashboard = useDashboardNavigation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [tenant, setTenant] = useState<TenantContextResponse | null>(null);
  // Sprint 56: hide the register link when self-signup is closed. Null
  // while loading so we don't briefly flash the link.
  const [registrationOpen, setRegistrationOpen] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    resolveTenant()
      .then((t) => { if (!cancelled) setTenant(t); })
      .catch(() => { if (!cancelled) setTenant(null); });
    getRegistrationOpen()
      .then((r) => { if (!cancelled) setRegistrationOpen(r.open); })
      .catch(() => { if (!cancelled) setRegistrationOpen(false); });
    return () => { cancelled = true; };
  }, []);

  // Capture SSO callback handoff: backend redirects to /login#sso_token=<jwt>
  useEffect(() => {
    if (typeof window === "undefined") return;
    const hash = window.location.hash;
    if (!hash.startsWith("#sso_token=")) return;
    const token = decodeURIComponent(hash.slice("#sso_token=".length));
    if (!token) return;
    setToken(token);
    // Clear hash so a refresh doesn't reprocess.
    window.history.replaceState(null, "", window.location.pathname);
    (async () => {
      try {
        await getMe();
        navigateDashboard("/dashboard");
      } catch (err) {
        setError(err instanceof Error ? err.message : "SSO login failed");
      }
    })();
  }, [router]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const outcome = await login(username, password);
      if (outcome.mfaRequired && outcome.mfaToken) {
        sessionStorage.setItem("opsmender_mfa_token", outcome.mfaToken);
        router.push("/mfa-challenge");
      } else if (outcome.mfaEnrollmentRequired) {
        router.push("/mfa-setup");
      } else {
        navigateDashboard("/dashboard");
      }
    } catch {
      setError("Invalid email or password.");
    } finally {
      setLoading(false);
    }
  }

  const ssoEnabled = Boolean(tenant?.sso_enabled && tenant.sso_login_path);
  const samlEnabled = Boolean(tenant?.saml_enabled && tenant.saml_login_path);

  return (
    <AuthShell
      title="Sign in to OpsMender"
      description="Detect. Mend. Prevail."
      eyebrow=""
      footer={
        registrationOpen ? (
          <>
            No account?{" "}
            <Link href="/register" className="font-medium text-accent-text hover:underline">
              Register
            </Link>
          </>
        ) : null
      }
    >
      {/* Email + password is the primary sign-in method and is always available.
          SSO, when configured, is offered as an alternative below. */}
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <FormAlert message={error} />}
        <div>
          <Label htmlFor="username" required>Email</Label>
          <Input
            id="username"
            type="email"
            name="username"
            autoComplete="email"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="you@example.com"
          />
        </div>

        <PasswordField
          id="password"
          name="password"
          label="Password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••"
        />
        <div className="-mt-2 text-right">
          <Link href="/password-reset" className="text-xs font-medium text-accent-text hover:underline">
            Forgot password?
          </Link>
        </div>

        <Button type="submit" loading={loading} className="w-full justify-center">
          Sign in
        </Button>
      </form>

      {(ssoEnabled || samlEnabled) && (
        <div className="mt-6 space-y-3">
          <div className="flex items-center gap-3 text-xs uppercase tracking-wide text-fg-muted">
            <span className="h-px flex-1 bg-border-subtle" />
            or
            <span className="h-px flex-1 bg-border-subtle" />
          </div>
          {ssoEnabled && (
            <a
              href={tenant!.sso_login_path!}
              className="flex w-full items-center justify-center gap-2 rounded-md border border-border-subtle bg-bg-elevated px-4 py-2.5 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
            >
              Sign in with {tenant?.org_name ?? "SSO"}
            </a>
          )}
          {samlEnabled && (
            <a
              href={tenant!.saml_login_path!}
              className="flex w-full items-center justify-center gap-2 rounded-md border border-border-subtle bg-bg-elevated px-4 py-2.5 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
            >
              Sign in with SAML
            </a>
          )}
        </div>
      )}
    </AuthShell>
  );
}
