import { AuthGuard } from "@/components/AuthGuard";
import { Sidebar } from "@/components/Sidebar";
import type { ReactNode } from "react";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <div className="flex h-screen overflow-hidden bg-bg-base">
        <Sidebar />
        <main className="flex-1 overflow-y-auto p-8 text-fg-primary">
          {children}
        </main>
      </div>
    </AuthGuard>
  );
}
