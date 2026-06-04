"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { useAuth } from "@/context/auth";
import { changeMyPassword } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { FormError } from "@/components/ui/Input";
import { PasswordField } from "@/components/ui/PasswordField";

/**
 * Forced password change. Reached when the signed-in user has a temporary
 * password (must_change_password). Dashboard access is blocked by AuthGuard
 * until the password is rotated here. This page lives outside /dashboard so it
 * is not itself guarded into a redirect loop.
 */
export default function PasswordChangeRequiredPage() {
  const router = useRouter();
  const { user, loading, refresh } = useAuth();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!loading && !user) router.push("/login");
    // If the flag is already cleared (e.g. navigated here directly), move on.
    else if (!loading && user && !user.must_change_password) router.push("/dashboard");
  }, [loading, user, router]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (next.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    if (next !== confirm) {
      setError("New password and confirmation don't match.");
      return;
    }
    setSubmitting(true);
    try {
      await changeMyPassword({ current_password: current, new_password: next });
      await refresh();
      router.push("/dashboard");
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : "Couldn't change the password. Check your current password.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell
      title="Set a new password"
      description="Your account is using a temporary password. Choose a new one to continue."
      eyebrow=""
      footer={null}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <PasswordField
          label="Current (temporary) password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
          autoFocus
          autoComplete="current-password"
        />
        <PasswordField
          label="New password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          minLength={8}
          required
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
          {submitting ? "Updating…" : "Update password & continue"}
        </Button>
      </form>
    </AuthShell>
  );
}
