"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { acceptInvite, getInviteByToken, setToken } from "@/lib/api";
import type { InvitePublicResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { FormError, Input, Label } from "@/components/ui/Input";
import { PasswordField } from "@/components/ui/PasswordField";
import { useDashboardNavigation } from "@/lib/use-dashboard-navigation";


/**
 * Public invite-accept page.
 *
 * Reads the token from ``?token=...`` (Next.js static-export friendly).
 * Validates the token via the public ``GET /invites/{token}``, then
 * renders a username + password form. On submit, POSTs
 * ``/invites/{token}/accept`` and drops the new user into the
 * dashboard with the returned JWT.
 */
export default function InviteAcceptPage() {
  return (
    <Suspense
      fallback={
        <AuthShell title="Loading invite…" description="" eyebrow="" footer={null}>
          <p className="text-sm text-fg-muted">Validating your invite…</p>
        </AuthShell>
      }
    >
      <InviteAcceptContent />
    </Suspense>
  );
}


function InviteAcceptContent() {
  const router = useRouter();
  const navigateDashboard = useDashboardNavigation();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [invite, setInvite] = useState<InvitePublicResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [username, setUsername] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  useEffect(() => {
    if (!token) {
      setLoading(false);
      setLoadError("No invite token in the URL.");
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const data = await getInviteByToken(token);
        if (!cancelled) {
          setInvite(data);
          // Suggest a username from the local part of the email so the
          // recipient doesn't have to invent one.
          setUsername(suggestUsername(data.email));
          // Prefill names the admin supplied on the invite (editable).
          if (data.first_name) setFirstName(data.first_name);
          if (data.last_name) setLastName(data.last_name);
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError(
            err instanceof Error
              ? err.message
              : "This invite link is invalid or has expired.",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitError("");
    setSubmitting(true);
    try {
      const resp = await acceptInvite(token, {
        username,
        password,
        first_name: firstName.trim() || null,
        last_name: lastName.trim() || null,
      });
      setToken(resp.access_token);
      // Drop straight into the dashboard. We don't have primary_org_id
      // in the response, but /auth/me on the next page load will set it.
      navigateDashboard("/dashboard");
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <AuthShell title="Loading invite…" description="" eyebrow="" footer={null}>
        <p className="text-sm text-fg-muted">Validating your invite…</p>
      </AuthShell>
    );
  }

  if (loadError || !invite) {
    return (
      <AuthShell
        title="Invite no longer valid"
        description="This link may have been used, revoked, or expired."
        eyebrow=""
        footer={
          <>
            Already have an account?{" "}
            <Link
              href="/login"
              className="font-medium text-accent-text hover:underline"
            >
              Sign in
            </Link>
          </>
        }
      >
        <p className="text-sm text-fg-secondary">
          {loadError ??
            "Ask the admin who invited you to send a fresh link."}
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={`Join ${invite.org_name}`}
      description={`You've been invited as ${invite.role}. Pick a username and password to finish setting up your account.`}
      eyebrow=""
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-accent-text hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <Label>Email</Label>
          <Input value={invite.email} disabled />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>First name</Label>
            <Input
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              maxLength={100}
              placeholder="Ada"
            />
          </div>
          <div>
            <Label>Last name</Label>
            <Input
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
              maxLength={100}
              placeholder="Lovelace"
            />
          </div>
        </div>
        <div>
          <Label>Username</Label>
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            minLength={3}
            maxLength={150}
            required
            autoFocus
          />
        </div>
        <PasswordField
          label="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={8}
          required
          autoComplete="new-password"
        />
        {submitError && <FormError message={submitError} />}
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? "Creating account…" : "Accept invite"}
        </Button>
      </form>
    </AuthShell>
  );
}


function suggestUsername(email: string): string {
  const local = email.split("@", 1)[0] ?? "";
  return local
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 150);
}
