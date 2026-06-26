"use client";

import { useCallback, useEffect, useState } from "react";
import { getOrgSlug, scopeDashboardHref } from "@/lib/org-path";

export function useDashboardHref(): (href: string) => string {
  const [orgSlug, setOrgSlug] = useState<string | null>(null);

  useEffect(() => {
    setOrgSlug(getOrgSlug());
  }, []);

  return useCallback(
    (href: string) => scopeDashboardHref(href, orgSlug ?? getOrgSlug()),
    [orgSlug],
  );
}
