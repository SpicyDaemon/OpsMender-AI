"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AlertOctagon } from "lucide-react";
import "./globals.css";

export default function GlobalError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error("[AIM] Unhandled application error:", error);
  }, [error]);

  return (
    <html lang="en" className="h-full">
      <body className="h-full">
        <title>Something went wrong · AIM</title>
        <div className="flex min-h-screen items-center justify-center bg-bg-base px-6">
          <div className="w-full max-w-md rounded-lg border border-border-subtle bg-bg-elevated p-8 text-center shadow-2xl">
            <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-md border border-status-critical-border bg-status-critical-bg text-status-critical">
              <AlertOctagon size={24} />
            </div>
            <p className="font-mono text-[11px] uppercase tracking-wider text-fg-muted">
              Error 500
            </p>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight text-fg-primary">
              Something went wrong
            </h1>
            <p className="mt-3 text-sm text-fg-secondary">
              The dashboard hit an unexpected error. Check the browser console or
              the backend logs for details.
            </p>
            {error.digest && (
              <p className="mt-3 font-mono text-[11px] text-fg-muted">
                Ref: {error.digest}
              </p>
            )}
            <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
              <button
                onClick={() => unstable_retry()}
                className="inline-flex items-center justify-center rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
              >
                Try again
              </button>
              <Link
                href="/dashboard/incidents"
                className="inline-flex items-center justify-center rounded-md border border-border-strong bg-bg-panel px-4 py-2 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
              >
                Back to dashboard
              </Link>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}
