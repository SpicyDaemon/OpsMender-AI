import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Not found · AIM",
  description: "The page you're looking for doesn't exist in AI Incident Manager.",
};

export default function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg-base px-6">
      <div className="w-full max-w-md rounded-lg border border-border-subtle bg-bg-elevated p-8 text-center shadow-2xl">
        <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-md border border-status-high-border bg-status-high-bg text-status-high">
          <AlertTriangle size={24} />
        </div>
        <p className="font-mono text-[11px] uppercase tracking-wider text-fg-muted">
          Error 404
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-fg-primary">
          Page not found
        </h1>
        <p className="mt-3 text-sm text-fg-secondary">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
          <Link
            href="/dashboard/incidents"
            className="inline-flex items-center justify-center rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
          >
            Back to dashboard
          </Link>
          <Link
            href="/login"
            className="inline-flex items-center justify-center rounded-md border border-border-strong bg-bg-panel px-4 py-2 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
          >
            Sign in
          </Link>
        </div>
      </div>
    </div>
  );
}
