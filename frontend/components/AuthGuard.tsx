"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/auth";
import { PageSpinner } from "@/components/ui/Spinner";

export function AuthGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.push("/login");
    } else if (user.must_change_password) {
      // Temporary password — no dashboard access until it's rotated.
      router.push("/password-change-required");
    } else if (user.mfa_enrollment_required) {
      router.push("/mfa-setup");
    }
  }, [loading, user, router]);

  if (loading) return <PageSpinner />;
  if (!user || user.must_change_password || user.mfa_enrollment_required) return null;

  return <>{children}</>;
}
