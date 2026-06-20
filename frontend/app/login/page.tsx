"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { FormError, Input, Label } from "@/components/ui/Input";
import { PasswordField } from "@/components/ui/PasswordField";
import {
  getMe,
  getRegistrationOpen,
  getSSOHint,
  resolveTenant,
  setOrgId,
  setToken,
} from "@/lib/api";
import type { SSOHintResponse, TenantContextResponse } from "@/lib/types";
import { setOrgSlug } from "@/lib/org-path";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [ssoHint, setSSOHint] = useState<SSOHintResponse | null>(null);
  const [hintLoading, setHintLoading] = useState(false);
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
        const me = await getMe();
        if (me.primary_org_id) setOrgId(me.primary_org_id);
        router.push("/dashboard");
      } catch (err) {
        setError(err instanceof Error ? err.message : "SSO login failed");
      }
    })();
  }, [router]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (
      ssoHint?.login_path &&
      (ssoHint.provider === "oidc" || ssoHint.provider === "saml")
    ) {
      setOrgSlug(ssoHint.org_slug ?? null);
      window.location.href = ssoHint.login_path;
      return;
    }
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
        router.push("/dashboard");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  async function resolveEmailHint() {
    const email = username.trim().toLowerCase();
    if (!email.includes("@")) {
      setSSOHint(null);
      return;
    }
    setHintLoading(true);
    try {
      setSSOHint(await getSSOHint(email));
    } catch {
      setSSOHint(null);
    } finally {
      setHintLoading(false);
    }
  }

  return (
    <AuthShell
      title="Sign in to OpsMender"
      description="Open the operator console and pick up the next incident with full session context."
      eyebrow=""
      footer={
        registrationOpen ? (
          <>
            No account?{" "}
            <Link href="/register" className="font-medium text-accent hover:underline">
              Register
            </Link>
          </>
        ) : registrationOpen === false ? (
          <span className="text-fg-muted">
            Self-signup is closed. Ask an admin for an invite.
          </span>
        ) : null
      }
    >
      {((tenant?.sso_enabled && tenant.sso_login_path) ||
        (tenant?.saml_enabled && tenant.saml_login_path)) && (
        <div className="mb-6 space-y-3">
          {tenant?.sso_enabled && tenant.sso_login_path && (
            <a
              href={tenant.sso_login_path}
              onClick={() => setOrgSlug(tenant.org_slug ?? null)}
              className="flex w-full items-center justify-center gap-2 rounded-md border border-border-subtle bg-bg-elevated px-4 py-2.5 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
            >
              Sign in with {tenant.org_name ?? "SSO"}
            </a>
          )}
          {tenant?.saml_enabled && tenant.saml_login_path && (
            <a
              href={tenant.saml_login_path}
              onClick={() => setOrgSlug(tenant.org_slug ?? null)}
              className="flex w-full items-center justify-center gap-2 rounded-md border border-border-subtle bg-bg-elevated px-4 py-2.5 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
            >
              Sign in with SAML
            </a>
          )}
          <div className="flex items-center gap-3 text-xs uppercase tracking-wide text-fg-muted">
            <span className="h-px flex-1 bg-border-subtle" />
            or use a local account
            <span className="h-px flex-1 bg-border-subtle" />
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <Label htmlFor="username">Email</Label>
          <Input
            id="username"
            type="email"
            name="username"
            autoComplete="email"
            required
            value={username}
            onChange={(e) => {
              setUsername(e.target.value);
              setSSOHint(null);
            }}
            onBlur={resolveEmailHint}
            placeholder="you@example.com"
          />
        </div>

        {ssoHint?.provider !== "oidc" && ssoHint?.provider !== "saml" ? (
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
        ) : null}

        {error && <FormError message={error} />}

        {ssoHint?.login_path &&
        (ssoHint.provider === "oidc" || ssoHint.provider === "saml") ? (
          <a
            href={ssoHint.login_path}
            onClick={() => setOrgSlug(ssoHint.org_slug ?? null)}
            className="flex w-full items-center justify-center rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
          >
            {ssoHint.label}
          </a>
        ) : (
          <Button
            type="submit"
            loading={loading || hintLoading}
            className="w-full justify-center"
          >
            Sign in
          </Button>
        )}
      </form>
    </AuthShell>
  );
}
