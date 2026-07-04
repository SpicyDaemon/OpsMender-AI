import { type ButtonHTMLAttributes, type ReactNode } from "react";

const VARIANTS = {
  primary: "bg-accent text-accent-contrast hover:bg-accent-hover border border-transparent",
  secondary: "bg-bg-panel text-fg-primary border border-border-strong hover:bg-bg-hover",
  danger: "bg-red-700 text-white hover:bg-red-800 border border-transparent",
  ghost: "text-fg-secondary hover:bg-bg-hover hover:text-fg-primary border border-transparent",
  success: "bg-emerald-700 text-white hover:bg-emerald-800 border border-transparent",
} as const;

const SIZES = {
  sm: "px-2.5 py-1 text-xs",
  md: "px-3.5 py-1.5 text-sm",
  lg: "px-5 py-2.5 text-sm",
} as const;

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof VARIANTS;
  size?: keyof typeof SIZES;
  loading?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  children,
  className = "",
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
    >
      {loading && (
        <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
      )}
      {children}
    </button>
  );
}
