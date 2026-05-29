"use client";

import { type CSSProperties, type ReactNode } from "react";
import { Search, X } from "lucide-react";
import { Input, Select } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

export interface FilterBarSelect {
  id: string;
  /** Accessible label for the dropdown. */
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}

/**
 * Shared filter/search bar for Paging tables. Extracted from the Services
 * filter bar so Teams, Escalation Chains, Rosters, Maintenance Windows, and
 * Notifications share one clean layout: search on the left, a wrapping row of
 * select dropdowns, then Clear + a primary action on the right.
 */
export function PagingFilterBar({
  search,
  onSearchChange,
  searchPlaceholder = "Search…",
  searchAriaLabel = "Search",
  filters = [],
  hasFilters,
  onClear,
  action,
}: {
  search: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder?: string;
  searchAriaLabel?: string;
  filters?: FilterBarSelect[];
  hasFilters: boolean;
  onClear: () => void;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-3 shadow-sm">
      {/* Grid (not flex): grid tracks constrain each control so the shared
          Select's `w-full` fills its track instead of forcing its own row.
          Stacks on small screens, collapses to a single row at xl — matching
          the Services filter bar. Column count is dynamic via a CSS var. */}
      <div
        className="grid gap-3 xl:[grid-template-columns:var(--paging-filter-cols)]"
        style={
          {
            "--paging-filter-cols": `minmax(16rem,1.25fr) repeat(${filters.length}, minmax(8rem,0.7fr)) auto`,
          } as CSSProperties
        }
      >
        <div className="relative">
          <Search
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
          />
          <Input
            aria-label={searchAriaLabel}
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={searchPlaceholder}
            className="h-11 pl-9"
          />
        </div>
        {filters.map((f) => (
          <Select
            key={f.id}
            aria-label={f.label}
            value={f.value}
            onChange={(e) => f.onChange(e.target.value)}
            className="h-11"
          >
            {f.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        ))}
        <div className="flex items-center justify-end gap-2">
          {hasFilters ? (
            <Button variant="ghost" size="sm" onClick={onClear} className="h-11">
              <X size={14} />
              Clear
            </Button>
          ) : null}
          {action}
        </div>
      </div>
    </div>
  );
}
