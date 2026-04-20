import { type ReactNode } from "react";
import { type LucideIcon } from "lucide-react";

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className = "",
}: EmptyStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-lg border border-dashed border-border-strong bg-bg-elevated px-6 py-12 text-center ${className}`}
    >
      {Icon && (
        <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-md bg-bg-panel text-fg-muted">
          <Icon size={20} />
        </div>
      )}
      <h3 className="text-sm font-semibold text-fg-primary">{title}</h3>
      {description && (
        <p className="mt-1 max-w-sm text-xs text-fg-secondary">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
