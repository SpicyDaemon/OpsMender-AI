type Option<V extends string> = {
  value: V;
  label: string;
  /** Optional trailing count shown as a subtle badge (e.g. "Pending 3"). */
  count?: number;
};

interface SegmentedControlProps<V extends string> {
  options: readonly Option<V>[];
  value: V;
  onChange: (v: V) => void;
  ariaLabel?: string;
  className?: string;
}

/**
 * A connected, mutually-exclusive "pick one view" control — for filtering a
 * single list by a small fixed set of options (unlike loose `FilterChips`,
 * which read as multi-select facets, and unlike navigation tabs, which change
 * the page). Optional per-option counts turn the filter into a glance-able
 * summary.
 */
export function SegmentedControl<V extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className = "",
}: SegmentedControlProps<V>) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={`inline-flex overflow-hidden rounded-lg border border-border-strong bg-bg-input ${className}`}
    >
      {options.map((opt, i) => {
        const isActive = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={isActive}
            className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-medium transition ${
              i > 0 ? "border-l border-border-subtle" : ""
            } ${
              isActive
                ? "bg-accent-bg text-accent-text shadow-[inset_0_0_0_1px_var(--color-accent)]"
                : "text-fg-secondary hover:bg-bg-hover hover:text-fg-primary"
            }`}
          >
            {opt.label}
            {opt.count != null && (
              <span
                className={`text-[11px] tabular-nums ${
                  isActive ? "text-accent-text" : "text-fg-muted"
                }`}
              >
                {opt.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
