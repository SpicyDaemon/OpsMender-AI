"use client";

import Link from "next/link";
import Image from "next/image";
import type { ReactNode } from "react";
import { useTheme } from "@/context/theme";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

interface AuthShellProps {
  title: string;
  description: string;
  footer: ReactNode;
  children: ReactNode;
  eyebrow?: string;
}

export function AuthShell({
  title,
  description,
  footer,
  children,
  eyebrow = "Operator Access",
}: AuthShellProps) {
  const { resolvedTheme } = useTheme();
  const iconSrc =
    resolvedTheme === "light"
      ? "/opsmender_icon_light_transparent.png"
      : "/opsmender_icon_dark_transparent.png";
  const wordmarkSrc =
    resolvedTheme === "light"
      ? "/opsmender_wordmark_light_transparent_clean.png"
      : "/opsmender_wordmark_dark_transparent_clean.png";

  return (
    <div className="relative flex min-h-full items-center justify-center overflow-hidden bg-bg-base px-4 py-16">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(59,130,246,0.16),transparent_30%),radial-gradient(circle_at_bottom_right,rgba(245,158,11,0.12),transparent_28%)]" />

      <div className="relative z-10 w-full max-w-md">
        <div className="mb-4 rounded-lg border border-border-subtle bg-bg-elevated/90 p-4 shadow-[0_18px_60px_rgba(0,0,0,0.35)] backdrop-blur">
          <div className="flex items-start justify-between gap-3">
            <Link href="/" className="flex items-center gap-3">
              <Image
                src={iconSrc}
                alt="OpsMender"
                width={44}
                height={44}
                className="shrink-0"
              />
              <Image
                src={wordmarkSrc}
                alt="OpsMender"
                width={577}
                height={117}
                className="h-7 w-auto"
                priority
              />
            </Link>
            <ThemeToggle compact />
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-border-subtle bg-bg-panel shadow-[0_24px_80px_rgba(0,0,0,0.45)]">
          <div className="border-b border-border-subtle bg-bg-elevated px-6 py-5">
            {eyebrow ? (
              <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-accent-text">
                {eyebrow}
              </p>
            ) : null}
            <h2 className={`${eyebrow ? "mt-2" : ""} text-2xl font-semibold tracking-tight text-fg-primary`}>
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
