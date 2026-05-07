"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { FormError, Input, Label } from "@/components/ui/Input";
import { PasswordField } from "@/components/ui/PasswordField";
import { getMe, resolveTenant, setOrgId, setToken } from "@/lib/api";
import type { TenantContextResponse } from "@/lib/types";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [tenant, setTenant] = useState<TenantContextResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    resolveTenant()
      .then((t) => { if (!cancelled) setTenant(t); })
      .catch(() => { if (!cancelled) setTenant(null); });
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
        router.push("/dashboard/incidents");
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
      await login(username, password);
      router.push("/dashboard/incidents");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title="Sign in to AIM"
      description="Open the operator console and pick up the next incident with full session context."
      eyebrow=""
      footer={(
        <>
          No account?{" "}
          <Link href="/register" className="font-medium text-accent hover:underline">
            Register
          </Link>
        </>
      )}
    >
      {((tenant?.sso_enabled && tenant.sso_login_path) ||
        (tenant?.saml_enabled && tenant.saml_login_path)) && (
        <div className="mb-6 space-y-3">
          {tenant?.sso_enabled && tenant.sso_login_path && (
            <a
              href={tenant.sso_login_path}
              className="flex w-full items-center justify-center gap-2 rounded-md border border-border-subtle bg-bg-elevated px-4 py-2.5 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
            >
              Sign in with {tenant.org_name ?? "SSO"}
            </a>
          )}
          {tenant?.saml_enabled && tenant.saml_login_path && (
            <a
              href={tenant.saml_login_path}
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
          <Label htmlFor="username">Username</Label>
          <Input
            id="username"
            type="text"
            name="username"
            autoComplete="username"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="admin"
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

        {error && <FormError message={error} />}

        <Button type="submit" loading={loading} className="w-full justify-center">
          Sign in
        </Button>
      </form>
    </AuthShell>
  );
}
