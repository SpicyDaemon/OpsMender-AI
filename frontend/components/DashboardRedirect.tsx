"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Client-side redirect helper for legacy/alias dashboard routes. */
export function DashboardRedirect({ to }: { to: string }) {
  const router = useRouter();
  useEffect(() => {
    router.replace(to);
  }, [router, to]);

  return null;
}
