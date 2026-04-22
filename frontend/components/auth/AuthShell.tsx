import Link from "next/link";
import type { ReactNode } from "react";
import { ShieldCheck } from "lucide-react";

interface AuthShellProps {
  title: string;
  description: string;
  footer: ReactNode;
  children: ReactNode;
}

export function AuthShell({ title, description, footer, children }: AuthShellProps) {
  return (
    <div className="relative flex min-h-full items-center justify-center overflow-hidden bg-bg-base px-4 py-16">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(59,130,246,0.16),transparent_30%),radial-gradient(circle_at_bottom_right,rgba(245,158,11,0.12),transparent_28%)]" />

      <div className="relative z-10 w-full max-w-md">
        <div className="mb-4 rounded-lg border border-border-subtle bg-bg-elevated/90 p-4 shadow-[0_18px_60px_rgba(0,0,0,0.35)] backdrop-blur">
          <Link href="/" className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-lg border border-accent/30 bg-accent-bg text-accent">
              <ShieldCheck size={20} />
            </div>
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-fg-muted">
                AIM Console
              </p>
              <h1 className="text-xl font-semibold tracking-tight text-fg-primary">
                AI Incident Manager
              </h1>
            </div>
          </Link>
        </div>

        <div className="overflow-hidden rounded-lg border border-border-subtle bg-bg-panel shadow-[0_24px_80px_rgba(0,0,0,0.45)]">
          <div className="border-b border-border-subtle bg-bg-elevated px-6 py-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-accent">
              Operator Access
            </p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight text-fg-primary">
              {title}
            </h2>
            <p className="mt-1 text-sm text-fg-secondary">{description}</p>
          </div>

          <div className="px-6 py-6">{children}</div>
        </div>

        <div className="mt-4 text-center text-sm text-fg-secondary">{footer}</div>
      </div>
    </div>
  );
}
