import { type ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}

export function Card({ children, className = "", padded = true }: CardProps) {
  return (
    <div
      className={`rounded-lg border border-border-subtle bg-bg-elevated ${padded ? "p-5" : ""} ${className}`}
    >
      {children}
    </div>
  );
}

interface CardHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function CardHeader({ title, description, actions, className = "" }: CardHeaderProps) {
  return (
    <div className={`flex items-start justify-between gap-4 ${className}`}>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-fg-primary">{title}</h3>
        {description && (
          <p className="mt-1 text-xs text-fg-secondary">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4 border-b border-border-subtle pb-5">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold text-fg-primary tracking-tight">{title}</h1>
        {description && (
          <p className="mt-1 text-sm text-fg-secondary">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}
