"use client";

import { useEffect } from "react";
import { scopeDashboardHref } from "@/lib/org-path";

export function DashboardRedirect({ to }: { to: string }) {
  useEffect(() => {
    window.location.replace(scopeDashboardHref(to));
  }, [to]);

  return null;
}
