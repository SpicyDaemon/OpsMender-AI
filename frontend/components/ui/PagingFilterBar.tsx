"use client";

import { type ReactNode } from "react";
import { Search, X } from "lucide-react";
import { Input, Select } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { FilterDropdown } from "@/components/ui/FilterDropdown";

interface FilterOption {
  value: string;
  label: string;
}

/** Multi-select checkbox dropdown (OR within the dimension; empty = all). */
export interface FilterBarMultiSelect {
  kind?: "multi";
  id: string;
  /** Accessible label + the dropdown's display label. */
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  options: FilterOption[];
}

/** Single-select dropdown — for inherently single-choice ranges. */
export interface FilterBarSingleSelect {
  kind: "single";
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: FilterOption[];
}

export type FilterBarFilter = FilterBarMultiSelect | FilterBarSingleSelect;

/**
 * Shared filter/search bar for Paging tables: search on the left, a row of
 * filter dropdowns, then Clear + a primary action on the right. Filter
 * dropdowns are multi-select checkbox popovers by default (OR within a
 * dimension; no selection means "all"); pass `kind: "single"` for a plain
 * single-choice select (e.g. a time range). Stacks on small screens, collapses
 * to one row at xl.
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
  filters?: FilterBarFilter[];
  hasFilters: boolean;
  onClear: () => void;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-3 shadow-sm">
      {/* Flex row matching the DataTable filterBar (MCP Servers): the search
          grows to fill, filter controls keep their natural width at a single
          consistent gap, and the Clear + primary action group is pushed to
          the right edge. Wraps on narrow screens. */}
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
        {filters.map((f) =>
          f.kind === "single" ? (
            <Select
              key={f.id}
              aria-label={f.label}
              value={f.value}
              onChange={(e) => f.onChange(e.target.value)}
              className="h-11 !w-auto min-w-[9rem]"
            >
              {f.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          ) : (
            <FilterDropdown
              key={f.id}
              label={f.label}
              options={f.options}
              selected={f.values}
              onToggle={(value) =>
                f.onChange(
                  f.values.includes(value)
                    ? f.values.filter((v) => v !== value)
                    : [...f.values, value],
                )
              }
            />
          ),
        )}
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
