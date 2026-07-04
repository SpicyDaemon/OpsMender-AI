type Option<V extends string> = { value: V; label: string };

interface FilterChipsProps<V extends string> {
  options: readonly Option<V>[];
  value: V;
  onChange: (v: V) => void;
  ariaLabel?: string;
  className?: string;
}

export function FilterChips<V extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className = "",
}: FilterChipsProps<V>) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={`flex flex-wrap gap-1.5 ${className}`}
    >
      {options.map((opt) => {
        const isActive = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={isActive}
            className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium transition ${
              isActive
                ? "bg-accent text-accent-contrast"
                : "border border-border-default bg-bg-surface text-fg-secondary hover:border-border-strong hover:text-fg-primary"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
