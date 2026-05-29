"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Search, X } from "lucide-react";

export interface MultiSelectOption {
  value: string;
  label: string;
  /** Optional secondary line shown under the label (e.g. role, team). */
  sublabel?: string;
}

/**
 * Checkbox + chips multi-select. Replaces native `<select multiple>` so
 * operators never need Ctrl/Cmd. Selected values render as removable chips
 * above a searchable checkbox list.
 *
 * When `ordered` is set, the selected array preserves selection order and
 * each chip gains move up / move down controls — used for ordered preference
 * lists (preferred MCP servers) and ordered rotations (roster members).
 */
export function MultiSelect({
  options,
  selected,
  onChange,
  ordered = false,
  searchable = true,
  placeholder = "Search…",
  emptyLabel = "No options available.",
  ariaLabel,
  maxHeightClass = "max-h-48",
}: {
  options: MultiSelectOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  ordered?: boolean;
  searchable?: boolean;
  placeholder?: string;
  emptyLabel?: string;
  ariaLabel?: string;
  maxHeightClass?: string;
}) {
  const [query, setQuery] = useState("");

  const optionByValue = useMemo(() => {
    const map = new Map<string, MultiSelectOption>();
    for (const o of options) map.set(o.value, o);
    return map;
  }, [options]);

  // Chip order: preserve selection order when `ordered`, else option order.
  const selectedChips = useMemo(() => {
    if (ordered) {
      return selected
        .map((v) => optionByValue.get(v))
        .filter((o): o is MultiSelectOption => Boolean(o));
    }
    return options.filter((o) => selected.includes(o.value));
  }, [ordered, options, optionByValue, selected]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        (o.sublabel ?? "").toLowerCase().includes(q),
    );
  }, [options, query]);

  const toggle = (value: string, on: boolean) => {
    if (on) {
      if (selected.includes(value)) return;
      onChange([...selected, value]);
    } else {
      onChange(selected.filter((v) => v !== value));
    }
  };

  const move = (value: string, dir: -1 | 1) => {
    const idx = selected.indexOf(value);
    if (idx < 0) return;
    const next = idx + dir;
    if (next < 0 || next >= selected.length) return;
    const copy = selected.slice();
    [copy[idx], copy[next]] = [copy[next], copy[idx]];
    onChange(copy);
  };

  return (
    <div className="space-y-2" aria-label={ariaLabel}>
      {selectedChips.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {selectedChips.map((o, idx) => (
            <li
              key={o.value}
              className="inline-flex items-center gap-1 rounded-full border border-border-default bg-bg-surface px-2 py-0.5 text-xs text-fg-primary"
            >
              {ordered && (
                <span className="font-mono tabular-nums text-fg-muted">
                  {idx + 1}.
                </span>
              )}
              <span className="font-medium">{o.label}</span>
              {ordered && (
                <span className="inline-flex items-center">
                  <button
                    type="button"
                    aria-label={`Move ${o.label} up`}
                    title="Move up"
                    disabled={idx === 0}
                    onClick={() => move(o.value, -1)}
                    className="rounded p-0.5 text-fg-muted hover:text-fg-primary disabled:opacity-30"
                  >
                    <ChevronUp size={12} />
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${o.label} down`}
                    title="Move down"
                    disabled={idx === selectedChips.length - 1}
                    onClick={() => move(o.value, 1)}
                    className="rounded p-0.5 text-fg-muted hover:text-fg-primary disabled:opacity-30"
                  >
                    <ChevronDown size={12} />
                  </button>
                </span>
              )}
              <button
                type="button"
                aria-label={`Remove ${o.label}`}
                title="Remove"
                onClick={() => toggle(o.value, false)}
                className="rounded p-0.5 text-fg-muted hover:text-status-critical"
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="rounded-md border border-border-strong bg-bg-input">
        {searchable && options.length > 6 && (
          <div className="relative border-b border-border-subtle">
            <Search
              size={14}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-muted"
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={placeholder}
              aria-label={ariaLabel ? `Filter ${ariaLabel}` : "Filter options"}
              className="w-full bg-transparent py-2 pl-8 pr-3 text-sm text-fg-primary placeholder:text-fg-muted outline-none"
            />
          </div>
        )}
        <ul className={`${maxHeightClass} overflow-y-auto p-1`}>
          {options.length === 0 ? (
            <li className="px-2 py-2 text-xs text-fg-muted">{emptyLabel}</li>
          ) : filtered.length === 0 ? (
            <li className="px-2 py-2 text-xs text-fg-muted">No matches.</li>
          ) : (
            filtered.map((o) => {
              const checked = selected.includes(o.value);
              return (
                <li key={o.value}>
                  <label className="flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 text-sm hover:bg-bg-hover">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => toggle(o.value, e.target.checked)}
                      className="mt-0.5"
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-fg-primary">
                        {o.label}
                      </span>
                      {o.sublabel && (
                        <span className="block truncate text-[11px] text-fg-muted">
                          {o.sublabel}
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              );
            })
          )}
        </ul>
      </div>
    </div>
  );
}
