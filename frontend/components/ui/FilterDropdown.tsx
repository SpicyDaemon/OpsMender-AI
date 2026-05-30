"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

export interface FilterDropdownOption {
  value: string;
  label: string;
}

/**
 * Multi-select checkbox filter dropdown (Services-style filter bar). No
 * Ctrl/Cmd — each option is a checkbox. Selecting multiple options is an OR
 * match; selecting none means "all" (the caller applies no filter for an empty
 * selection). The trigger reads `All <label>` when empty and `<label> · N`
 * when N options are selected.
 */
export function FilterDropdown({
  label,
  options,
  selected,
  onToggle,
  className = "",
}: {
  label: string;
  options: FilterDropdownOption[];
  /** Currently-selected option values. */
  selected: string[];
  /** Toggle one option value on/off. */
  onToggle: (value: string) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const count = selected.length;
  const display = count === 0 ? `All ${label}` : `${label} · ${count}`;

  return (
    <div ref={ref} className={`relative ${className}`}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex h-11 min-w-[9rem] items-center justify-between gap-2 rounded-md border border-border-strong bg-bg-input px-3 text-sm text-fg-primary"
      >
        <span className="truncate">{display}</span>
        <ChevronDown size={15} className="shrink-0 text-fg-muted" />
      </button>
      {open && (
        <div className="absolute z-30 mt-1 max-h-64 w-56 overflow-y-auto rounded-md border border-border-strong bg-bg-elevated p-1 shadow-lg">
          {options.length === 0 ? (
            <p className="px-2 py-2 text-xs text-fg-muted">No options.</p>
          ) : (
            options.map((o) => (
              <label
                key={o.value}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-bg-hover"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(o.value)}
                  onChange={() => onToggle(o.value)}
                />
                <span className="truncate">{o.label}</span>
              </label>
            ))
          )}
        </div>
      )}
    </div>
  );
}
