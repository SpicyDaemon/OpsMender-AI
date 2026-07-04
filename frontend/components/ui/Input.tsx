import { type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes, forwardRef } from "react";

const BASE = "block w-full rounded-md border border-border-strong bg-bg-input px-3 py-2 text-sm text-fg-primary placeholder:text-fg-muted transition-colors focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:bg-bg-hover disabled:opacity-60";
const TEMPORAL_INPUT_TYPES = new Set(["date", "datetime-local", "month", "time", "week"]);

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className = "", type, ...props }, ref) => {
    const nativeDateClass =
      typeof type === "string" && TEMPORAL_INPUT_TYPES.has(type)
        ? "opsmender-date-input"
        : "";
    return (
      <input
        ref={ref}
        type={type}
        className={`${BASE} ${nativeDateClass} ${className}`}
        {...props}
      />
    );
  },
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
    <select
      ref={ref}
      className={`${BASE} opsmender-select appearance-none pr-8 ${className}`}
      {...props}
    />
  ),
);
Select.displayName = "Select";

interface LabelProps {
  children: React.ReactNode;
  htmlFor?: string;
  className?: string;
  /**
   * Render the shared red-asterisk required marker after the label text.
   * This is the canonical way to flag a mandatory field across the app — keep
   * the visual star in sync with the input's own `required`/validation.
   */
  required?: boolean;
}
export function Label({ children, htmlFor, className = "", required = false }: LabelProps) {
  // The required marker is a CSS ::after pseudo-element rather than a real text
  // node so it never leaks into the label's textContent — `getByLabelText` and
  // the accessibility name stay clean; the input's own `required` attribute is
  // what assistive tech announces.
  const requiredMarker = required
    ? " after:ml-0.5 after:text-status-critical after:content-['*']"
    : "";
  return (
    <label
      htmlFor={htmlFor}
      className={`block text-xs font-medium text-fg-secondary mb-1.5 uppercase tracking-wide${requiredMarker} ${className}`}
    >
      {children}
    </label>
  );
}

export function FormError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="mt-1 text-xs text-status-critical">{message}</p>;
}

/**
 * Prominent form-level alert banner — for errors that aren't tied to a single
 * field (failed login, save failures, permission errors). Unlike the inline
 * {@link FormError}, this is impossible to miss. Defaults to the critical tone.
 */
export function FormAlert({
  message,
  tone = "error",
}: {
  message?: string;
  tone?: "error" | "success" | "info";
}) {
  if (!message) return null;
  const toneClass =
    tone === "success"
      ? "border-status-low-border bg-status-low-bg/40 text-status-low"
      : tone === "info"
        ? "border-border-strong bg-bg-elevated text-fg-secondary"
        : "border-status-critical-border bg-status-critical-bg/40 text-status-critical";
  return (
    <div
      role="alert"
      className={`rounded-md border px-3 py-2.5 text-sm ${toneClass}`}
    >
      {message}
    </div>
  );
}
