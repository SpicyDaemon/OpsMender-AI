"use client";

import { type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { useAuth } from "@/context/auth";
import { requiredRolesForPath } from "@/components/Sidebar";
import { stripOrgScope } from "@/lib/org-path";

/**
 * Per-route authorization guard. Mirrors the sidebar role model so a user who
 * navigates directly to a restricted URL (not just hides it in the nav) sees a
 * clean access-denied panel instead of admin data. Backend routes enforce the
 * same boundaries — this is the UI half of "do not rely only on hiding UI".
 */
export function RouteRoleGuard({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user } = useAuth();

  const allowed = requiredRolesForPath(stripOrgScope(pathname ?? ""));
  if (user && allowed && !allowed.includes(user.role)) {
    return (
      <div className="mx-auto flex max-w-md flex-col items-center justify-center py-20 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-status-high-bg text-status-high">
          <ShieldAlert size={22} />
        </div>
        <h1 className="mt-4 text-lg font-semibold text-fg-primary">Access denied</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Your role ({user.role}) doesn&apos;t have access to this page. Ask an
          administrator if you need it.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
