import { type ReactNode } from "react";

const VARIANTS = {
  // severity
  critical: "bg-status-critical-bg text-status-critical border-status-critical-border",
  high: "bg-status-high-bg text-status-high border-status-high-border",
  medium: "bg-status-medium-bg text-status-medium border-status-medium-border",
  low: "bg-status-low-bg text-status-low border-status-low-border",
  // incident status
  open: "bg-status-info-bg text-status-info border-status-info-border",
  in_progress: "bg-status-info-bg text-status-info border-status-info-border",
  resolved: "bg-status-low-bg text-status-low border-status-low-border",
  closed: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
  merged: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
  // session status
  active: "bg-status-info-bg text-status-info border-status-info-border",
  awaiting_approval: "bg-status-high-bg text-status-high border-status-high-border",
  running: "bg-status-info-bg text-status-info border-status-info-border",
  completed: "bg-status-low-bg text-status-low border-status-low-border",
  failed: "bg-status-critical-bg text-status-critical border-status-critical-border",
  timed_out: "bg-status-high-bg text-status-high border-status-high-border",
  paused: "bg-status-high-bg text-status-high border-status-high-border",
  stopped: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
  // approval status
  pending: "bg-status-high-bg text-status-high border-status-high-border",
  approved: "bg-status-low-bg text-status-low border-status-low-border",
  rejected: "bg-status-critical-bg text-status-critical border-status-critical-border",
  redirected: "bg-status-info-bg text-status-info border-status-info-border",
  expired: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
  // generic
  default: "bg-status-neutral-bg text-fg-secondary border-status-neutral-border",
  info: "bg-status-info-bg text-status-info border-status-info-border",
} as const;

type Variant = keyof typeof VARIANTS;

interface BadgeProps {
  variant?: Variant;
  children: ReactNode;
  className?: string;
}

export function Badge({ variant = "default", children, className = "" }: BadgeProps) {
  const cls = VARIANTS[variant] ?? VARIANTS.default;
  return (
    <span
      className={`inline-flex items-center rounded-pill border px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide ${cls} ${className}`}
    >
      {children}
    </span>
  );
}
