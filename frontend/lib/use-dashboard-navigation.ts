"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { isOrgScopedDashboardHref, scopeDashboardHref } from "@/lib/org-path";

type NavigateOptions = {
  replace?: boolean;
};

export function useDashboardNavigation(): (href: string, options?: NavigateOptions) => void {
  const router = useRouter();

  return useCallback(
    (href: string, options: NavigateOptions = {}) => {
      const target = scopeDashboardHref(href);
      if (isOrgScopedDashboardHref(target) && typeof window !== "undefined") {
        if (options.replace) window.location.replace(target);
        else window.location.assign(target);
        return;
      }
      if (options.replace) router.replace(target);
      else router.push(target);
    },
    [router],
  );
}
