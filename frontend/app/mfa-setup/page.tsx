"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { MFASettings } from "@/components/MFASettings";
import { PageSpinner } from "@/components/ui/Spinner";
import { useAuth } from "@/context/auth";
import { scopeDashboardHref } from "@/lib/org-path";

export default function MFASetupPage() {
  const { user, loading, refresh } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, router, user]);

  if (loading || !user) return <PageSpinner />;

  async function finish() {
    await refresh();
    window.location.href = scopeDashboardHref("/dashboard");
  }

  return (
    <AuthShell
      title="Set up multi-factor authentication"
      description="Your organization requires an authenticator code for local sign-in."
      eyebrow="Required security"
      footer={null}
    >
      <MFASettings enrollmentOnly onEnabled={finish} />
    </AuthShell>
  );
}
