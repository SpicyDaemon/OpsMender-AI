import { AuthGuard } from "@/components/AuthGuard";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import type { ReactNode } from "react";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <div className="flex h-screen overflow-hidden bg-bg-base">
        <Sidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          <TopBar />
          <main className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8 text-fg-primary">
            {children}
          </main>
        </div>
        <KeyboardShortcuts />
      </div>
    </AuthGuard>
  );
}
