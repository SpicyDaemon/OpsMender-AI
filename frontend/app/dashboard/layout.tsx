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
import {
  getOrgSlug,
  isDashboardHref,
  isOrgScopedDashboardHref,
  scopeDashboardHref,
  scopeDashboardPath,
} from "@/lib/org-path";

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

  useEffect(() => {
    function scopeRenderedDashboardAnchors() {
      const anchors = document.querySelectorAll<HTMLAnchorElement>("a[href]");
      anchors.forEach((anchor) => {
        const rawHref = anchor.getAttribute("href");
        if (!rawHref || !isDashboardHref(rawHref)) return;
        const scopedHref = scopeDashboardHref(rawHref);
        if (scopedHref !== rawHref) anchor.setAttribute("href", scopedHref);
      });
    }

    function onDocumentClick(event: MouseEvent) {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest<HTMLAnchorElement>("a[href]");
      if (!anchor || anchor.target || anchor.hasAttribute("download")) return;
      const rawHref = anchor.getAttribute("href");
      if (!rawHref) return;
      const scopedHref = scopeDashboardHref(rawHref);
      if (scopedHref === rawHref && !isOrgScopedDashboardHref(scopedHref)) return;
      event.preventDefault();
      window.location.assign(scopedHref);
    }

    scopeRenderedDashboardAnchors();
    const observer = new MutationObserver(scopeRenderedDashboardAnchors);
    observer.observe(document.body, {
      attributeFilter: ["href"],
      attributes: true,
      childList: true,
      subtree: true,
    });
    window.addEventListener(
      "opsmender:org-slug-updated",
      scopeRenderedDashboardAnchors,
    );
    document.addEventListener("click", onDocumentClick, true);
    return () => {
      observer.disconnect();
      window.removeEventListener(
        "opsmender:org-slug-updated",
        scopeRenderedDashboardAnchors,
      );
      document.removeEventListener("click", onDocumentClick, true);
    };
  }, []);

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
