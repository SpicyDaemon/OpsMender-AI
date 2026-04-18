import { type ReactNode } from "react";

const VARIANTS = {
  // severity
  critical: "bg-red-100 text-red-800 border-red-200",
  high: "bg-orange-100 text-orange-800 border-orange-200",
  medium: "bg-yellow-100 text-yellow-800 border-yellow-200",
  low: "bg-green-100 text-green-800 border-green-200",
  // status
  open: "bg-blue-100 text-blue-800 border-blue-200",
  in_progress: "bg-indigo-100 text-indigo-800 border-indigo-200",
  resolved: "bg-green-100 text-green-800 border-green-200",
  closed: "bg-gray-100 text-gray-600 border-gray-200",
  active: "bg-indigo-100 text-indigo-800 border-indigo-200",
  awaiting_approval: "bg-yellow-100 text-yellow-800 border-yellow-200",
  running: "bg-indigo-100 text-indigo-800 border-indigo-200",
  completed: "bg-green-100 text-green-800 border-green-200",
  failed: "bg-red-100 text-red-800 border-red-200",
  timed_out: "bg-orange-100 text-orange-800 border-orange-200",
  paused: "bg-yellow-100 text-yellow-800 border-yellow-200",
  // approval status
  pending: "bg-yellow-100 text-yellow-800 border-yellow-200",
  approved: "bg-green-100 text-green-800 border-green-200",
  rejected: "bg-red-100 text-red-800 border-red-200",
  expired: "bg-gray-100 text-gray-600 border-gray-200",
  // generic
  default: "bg-gray-100 text-gray-700 border-gray-200",
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
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${cls} ${className}`}
    >
      {children}
    </span>
  );
}
