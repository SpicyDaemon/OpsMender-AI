"use client";

import { type ReactNode } from "react";
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
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[16rem] flex-1">
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
            className="h-11 min-w-[8rem]"
          >
            {f.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        ))}
        <div className="ml-auto flex items-center gap-2">
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
