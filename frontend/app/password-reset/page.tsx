"use client";

import { Suspense, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { consumePasswordReset } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { FormError } from "@/components/ui/Input";
import { PasswordField } from "@/components/ui/PasswordField";

/**
 * Public password-reset page. Reads the one-time token from `?token=` (static
 * export friendly) and POSTs a new password to `/auth/password-reset/{token}`.
 * Invalid/expired tokens surface a clean error rather than a 404.
 */
export default function PasswordResetPage() {
  return (
    <Suspense
      fallback={
        <AuthShell title="Password reset" description="" eyebrow="" footer={null}>
          <p className="text-sm text-fg-muted">Loading…</p>
        </AuthShell>
      }
    >
      <PasswordResetContent />
    </Suspense>
  );
}

function PasswordResetContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const signInFooter = (
    <>
      Remembered it?{" "}
      <Link href="/login" className="font-medium text-accent hover:underline">
        Sign in
      </Link>
    </>
  );

  if (!token) {
    return (
      <AuthShell
        title="Reset link incomplete"
        description="This password-reset link is missing its token."
        eyebrow=""
        footer={signInFooter}
      >
        <p className="text-sm text-fg-secondary">
          Ask an administrator to send you a fresh reset link.
        </p>
      </AuthShell>
    );
  }

  if (done) {
    return (
      <AuthShell
        title="Password updated"
        description="Your password has been changed."
        eyebrow=""
        footer={signInFooter}
      >
        <Button className="w-full" onClick={() => router.push("/login")}>
          Continue to sign in
        </Button>
      </AuthShell>
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    setSubmitting(true);
    try {
      await consumePasswordReset(token, password);
      setDone(true);
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : "This reset link is invalid or has expired. Ask for a fresh one.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell
      title="Set a new password"
      description="Choose a new password for your OpsMender account."
      eyebrow=""
      footer={signInFooter}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <PasswordField
          label="New password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={8}
          required
          autoFocus
          autoComplete="new-password"
        />
        <PasswordField
          label="Confirm new password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          minLength={8}
          required
          autoComplete="new-password"
        />
        {error && <FormError message={error} />}
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? "Updating…" : "Update password"}
        </Button>
      </form>
    </AuthShell>
  );
}
