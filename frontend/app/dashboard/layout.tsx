"use client";

import { useState } from "react";
import { AuthGuard } from "@/components/AuthGuard";
import { RouteRoleGuard } from "@/components/RouteRoleGuard";
import { CommandPalette } from "@/components/CommandPalette";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { LiveEventsProvider } from "@/context/liveEvents";
import type { ReactNode } from "react";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  return (
    <AuthGuard>
      <>
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:border focus:border-border-strong focus:bg-bg-panel focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-fg-primary focus:shadow-lg"
        >
          Skip to content
        </a>
        <LiveEventsProvider>
          <div className="flex h-screen overflow-hidden bg-bg-base">
            <Sidebar
              mobileOpen={mobileSidebarOpen}
              onMobileClose={() => setMobileSidebarOpen(false)}
            />
            <div className="flex flex-1 flex-col overflow-hidden">
              <TopBar onOpenMobileNav={() => setMobileSidebarOpen(true)} />
              <main
                id="main-content"
                tabIndex={-1}
                className="flex-1 overflow-y-auto p-4 text-fg-primary sm:p-6 lg:p-8"
              >
                <RouteRoleGuard>{children}</RouteRoleGuard>
              </main>
            </div>
            <KeyboardShortcuts />
            <CommandPalette />
          </div>
        </LiveEventsProvider>
      </>
    </AuthGuard>
  );
}
