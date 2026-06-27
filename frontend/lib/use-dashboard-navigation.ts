"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";

type NavigateOptions = {
  replace?: boolean;
};

/**
 * Thin wrapper around the Next.js router for dashboard navigation.
 *
 * OpsMender runs a single organization per instance, so navigation targets are
 * plain `/dashboard/...` paths with no tenant scoping.
 */
export function useDashboardNavigation(): (href: string, options?: NavigateOptions) => void {
  const router = useRouter();

  return useCallback(
    (href: string, options: NavigateOptions = {}) => {
      if (options.replace) router.replace(href);
      else router.push(href);
    },
    [router],
  );
}
