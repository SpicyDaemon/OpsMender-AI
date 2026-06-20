"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { AuthGuard } from "@/components/AuthGuard";
import { RouteRoleGuard } from "@/components/RouteRoleGuard";
import { CommandPalette } from "@/components/CommandPalette";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import type { ReactNode } from "react";
import { getOrgSlug, scopeDashboardPath } from "@/lib/org-path";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    if (!pathname.startsWith("/dashboard")) return;
    const slug = getOrgSlug();
    if (!slug) return;
    const target = scopeDashboardPath(pathname, slug);
    window.location.replace(
      `${target}${window.location.search}${window.location.hash}`,
    );
  }, [pathname]);

  return (
    <AuthGuard>
      <div className="flex h-screen overflow-hidden bg-bg-base">
        <Sidebar
          mobileOpen={mobileSidebarOpen}
          onMobileClose={() => setMobileSidebarOpen(false)}
        />
        <div className="flex flex-1 flex-col overflow-hidden">
          <TopBar onOpenMobileNav={() => setMobileSidebarOpen(true)} />
          <main className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8 text-fg-primary">
            <RouteRoleGuard>{children}</RouteRoleGuard>
          </main>
        </div>
        <KeyboardShortcuts />
        <CommandPalette />
      </div>
    </AuthGuard>
  );
}
