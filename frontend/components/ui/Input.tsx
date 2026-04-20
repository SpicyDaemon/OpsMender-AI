import { type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes, forwardRef } from "react";

const BASE = "block w-full rounded-md border border-border-strong bg-bg-input px-3 py-2 text-sm text-fg-primary placeholder:text-fg-muted transition-colors focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:bg-bg-hover disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className = "", ...props }, ref) => (
    <input ref={ref} className={`${BASE} ${className}`} {...props} />
  ),
);
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className = "", ...props }, ref) => (
    <textarea ref={ref} className={`${BASE} ${className}`} rows={3} {...props} />
  ),
);
Textarea.displayName = "Textarea";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className = "", ...props }, ref) => (
    <select ref={ref} className={`${BASE} appearance-none pr-8 ${className}`} {...props} />
  ),
);
Select.displayName = "Select";

interface LabelProps {
  children: React.ReactNode;
  htmlFor?: string;
  className?: string;
}
export function Label({ children, htmlFor, className = "" }: LabelProps) {
  return (
    <label htmlFor={htmlFor} className={`block text-xs font-medium text-fg-secondary mb-1.5 uppercase tracking-wide ${className}`}>
      {children}
    </label>
  );
}

export function FormError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="mt-1 text-xs text-status-critical">{message}</p>;
}
