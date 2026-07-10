"use client";

import {
  Fragment,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Calendar as CalIcon,
  ChevronDown,
  ChevronRight,
  Columns3,
  Search,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/Button";
import { FilterDropdown } from "@/components/ui/FilterDropdown";
import { Input, Label, Select } from "@/components/ui/Input";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface FilterChipOption {
  value: string;
  label: string;
}

export interface DataTableColumn<T> {
  /** Stable identifier; used for storage keys + show/hide state. */
  id: string;
  /** Header label rendered in the column header. */
  label: string;
  /** Sortable + filter-chip + search source. Should return a primitive. */
  accessor: (row: T) => string | number | boolean | null | undefined;
  /** Optional custom cell renderer. Defaults to String(accessor(row)). */
  cell?: (row: T) => ReactNode;
  /** Allow clicking the header to sort by this column. */
  sortable?: boolean;
  /** Render multi-select filter chips above the table for this column. */
  filterChips?: {
    options: FilterChipOption[];
    /** Which filter-chip option value this row corresponds to. */
    valueOf: (row: T) => string | null | undefined;
  };
  /** Include this column in the top search box. */
  searchable?: boolean;
  /** Cell text alignment. */
  align?: "left" | "right" | "center";
  /** Hidden by default; operator can re-enable via the Columns toggle. */
  hiddenByDefault?: boolean;
}

export interface DateRangeColumnConfig<T> {
  /** Column id this range is bound to (only for display/labeling). */
  id: string;
  /** Optional label rendered alongside the range picker. */
  label?: string;
  /** Return a Date or ISO string for this row, or null to skip. */
  valueOf: (row: T) => Date | string | null | undefined;
}

export interface DataTableProps<T> {
  rows: T[];
  columns: DataTableColumn<T>[];
  rowKey: (row: T) => string;
  /** Optional custom phone card layout rendered below `md`. */
  phoneLayout?: (row: T) => ReactNode;
  /** Optional date-range picker driven by one column. */
  dateRangeColumn?: DateRangeColumnConfig<T>;
  /** Optional row-action slot rendered in a trailing cell. */
  rowActions?: (row: T) => ReactNode;
  /** Optional expanded detail row rendered below a row. */
  expandedRow?: {
    expandedKeys: Set<string>;
    onToggle: (key: string, row: T) => void;
    render: (row: T) => ReactNode;
    label?: string;
  };
  /** Sprint 50 — enable row-selection checkboxes. */
  selectable?: boolean;
  /** Sprint 50 — controlled selection (a Set of row keys). */
  selectedKeys?: Set<string>;
  /** Sprint 50 — fires whenever the selection set changes. */
  onSelectionChange?: (next: Set<string>) => void;
  /** Sprint 50 — rendered above the table when at least one row is selected. */
  bulkActions?: (selected: Set<string>, rows: T[]) => ReactNode;
  pageSizeOptions?: number[];
  defaultPageSize?: number;
  /** Empty-state node when the filtered result set is empty. */
  empty?: ReactNode;
  /** Persist sort/filter/columns state to localStorage under this key. */
  storageKey?: string;
  /** Top-right slot for additional controls (e.g. a "New …" button). */
  toolbarRight?: ReactNode;
  /** Hide the built-in toolbar when a page provides its own controls. */
  hideToolbar?: boolean;
  /**
   * Render the toolbar as a single Services-style filter row: search +
   * multi-select checkbox filter dropdowns (from each column's `filterChips`)
   * + `toolbarRight`, instead of the stacked search-row + inline chip rows.
   * Filtering semantics are unchanged (OR within a dimension, AND across).
   */
  filterBar?: boolean;
  /** Optional placeholder for the search input. */
  searchPlaceholder?: string;
  /** Pass-through className for the outer wrapper. */
  className?: string;
}

// ---------------------------------------------------------------------------
// Internal types + helpers
// ---------------------------------------------------------------------------

type SortDir = "asc" | "desc" | null;

interface SortState {
  columnId: string | null;
  dir: SortDir;
}

interface PersistedState {
  hiddenColumnIds?: string[];
  pageSize?: number;
}

function compareValues(a: unknown, b: unknown): number {
  if (a == null && b == null) return 0;
  if (a == null) return -1;
  if (b == null) return 1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

function toDate(v: Date | string | null | undefined): Date | null {
  if (v == null) return null;
  if (v instanceof Date) return v;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtDateTimeLocal(d: Date): string {
  // Renders into a <input type="datetime-local"> friendly string in local tz.
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function paginationItems(currentPage: number, totalPages: number): Array<number | "ellipsis"> {
  if (totalPages <= 6) {
    return Array.from({ length: totalPages }, (_, index) => index);
  }

  const pages = new Set<number>([
    0,
    totalPages - 1,
    currentPage - 1,
    currentPage,
    currentPage + 1,
  ]);
  if (currentPage <= 2) {
    pages.add(1);
    pages.add(2);
  }
  if (currentPage >= totalPages - 3) {
    pages.add(totalPages - 2);
    pages.add(totalPages - 3);
  }

  const ordered = Array.from(pages)
    .filter((page) => page >= 0 && page < totalPages)
    .sort((a, b) => a - b);
  const items: Array<number | "ellipsis"> = [];
  for (const page of ordered) {
    const previous = items[items.length - 1];
    if (typeof previous === "number" && page - previous > 1) {
      items.push("ellipsis");
    }
    items.push(page);
  }
  return items;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  phoneLayout,
  dateRangeColumn,
  rowActions,
  expandedRow,
  selectable = false,
  selectedKeys,
  onSelectionChange,
  bulkActions,
  pageSizeOptions = [10, 25, 50, 100],
  defaultPageSize = 25,
  empty,
  storageKey,
  toolbarRight,
  hideToolbar = false,
  filterBar = false,
  searchPlaceholder = "Search…",
  className = "",
}: DataTableProps<T>) {
  // Persistence — read on mount only.
  const [persisted] = useState<PersistedState>(() =>
    _loadPersisted(storageKey),
  );

  const [search, setSearch] = useState("");
  const [sortState, setSortState] = useState<SortState>({
    columnId: null,
    dir: null,
  });
  const [chipFilters, setChipFilters] = useState<Record<string, Set<string>>>(
    {},
  );
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(() => {
    const initial = new Set<string>(persisted.hiddenColumnIds ?? []);
    for (const col of columns) {
      if (col.hiddenByDefault && !initial.has(col.id)) initial.add(col.id);
    }
    return initial;
  });
  const [pageSize, setPageSize] = useState<number>(
    persisted.pageSize ?? defaultPageSize,
  );
  const [page, setPage] = useState(0);
  const [columnsMenuOpen, setColumnsMenuOpen] = useState(false);

  // Persist on change.
  useEffect(() => {
    if (!storageKey) return;
    try {
      localStorage.setItem(
        storageKey,
        JSON.stringify({
          hiddenColumnIds: Array.from(hiddenIds),
          pageSize,
        } satisfies PersistedState),
      );
    } catch {
      // localStorage can throw in private modes; ignore.
    }
  }, [storageKey, hiddenIds, pageSize]);

  const visibleColumns = useMemo(
    () => columns.filter((c) => !hiddenIds.has(c.id)),
    [columns, hiddenIds],
  );

  const searchableColumns = useMemo(
    () => columns.filter((c) => c.searchable),
    [columns],
  );

  const chipColumns = useMemo(
    () => columns.filter((c) => c.filterChips),
    [columns],
  );

  // ----- Apply filters --------------------------------------------------------
  const filteredRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const fromTs = dateFrom ? new Date(dateFrom).getTime() : null;
    const toTs = dateTo ? new Date(dateTo).getTime() : null;

    return rows.filter((row) => {
      // Search across searchable columns
      if (q && searchableColumns.length > 0) {
        const hit = searchableColumns.some((col) => {
          const v = col.accessor(row);
          return v != null && String(v).toLowerCase().includes(q);
        });
        if (!hit) return false;
      }

      // Chip filters (a row passes if, for every column with active chips,
      // its valueOf belongs to the selected set)
      for (const col of chipColumns) {
        const active = chipFilters[col.id];
        if (!active || active.size === 0) continue;
        const v = col.filterChips!.valueOf(row);
        if (v == null || !active.has(String(v))) return false;
      }

      // Date range
      if (dateRangeColumn && (fromTs != null || toTs != null)) {
        const d = toDate(dateRangeColumn.valueOf(row));
        if (d == null) return false;
        const t = d.getTime();
        if (fromTs != null && t < fromTs) return false;
        if (toTs != null && t > toTs) return false;
      }

      return true;
    });
  }, [
    rows,
    search,
    searchableColumns,
    chipColumns,
    chipFilters,
    dateRangeColumn,
    dateFrom,
    dateTo,
  ]);

  // ----- Sort -----------------------------------------------------------------
  const sortedRows = useMemo(() => {
    if (!sortState.columnId || !sortState.dir) return filteredRows;
    const col = columns.find((c) => c.id === sortState.columnId);
    if (!col) return filteredRows;
    const dir = sortState.dir === "asc" ? 1 : -1;
    return [...filteredRows].sort(
      (a, b) => dir * compareValues(col.accessor(a), col.accessor(b)),
    );
  }, [filteredRows, sortState, columns]);

  // ----- Paginate -------------------------------------------------------------
  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pageRows = useMemo(
    () => sortedRows.slice(safePage * pageSize, (safePage + 1) * pageSize),
    [sortedRows, safePage, pageSize],
  );
  const pageItems = useMemo(
    () => paginationItems(safePage, totalPages),
    [safePage, totalPages],
  );
  const firstVisibleRow = sortedRows.length === 0 ? 0 : safePage * pageSize + 1;
  const lastVisibleRow = Math.min((safePage + 1) * pageSize, sortedRows.length);

  // ----- Handlers -------------------------------------------------------------
  const cycleSort = (columnId: string) => {
    setPage(0);
    setSortState((prev) => {
      if (prev.columnId !== columnId) return { columnId, dir: "asc" };
      if (prev.dir === "asc") return { columnId, dir: "desc" };
      if (prev.dir === "desc") return { columnId: null, dir: null };
      return { columnId, dir: "asc" };
    });
  };

  const toggleChip = (columnId: string, value: string) => {
    setPage(0);
    setChipFilters((prev) => {
      const next = { ...prev };
      const cur = new Set(next[columnId] ?? []);
      if (cur.has(value)) cur.delete(value);
      else cur.add(value);
      next[columnId] = cur;
      return next;
    });
  };

  const clearAllFilters = () => {
    setPage(0);
    setSearch("");
    setChipFilters({});
    setDateFrom("");
    setDateTo("");
  };

  const setLast7Days = () => {
    setPage(0);
    const to = new Date();
    const from = new Date();
    from.setDate(from.getDate() - 7);
    setDateFrom(fmtDateTimeLocal(from));
    setDateTo(fmtDateTimeLocal(to));
  };

  const setLast30Days = () => {
    setPage(0);
    const to = new Date();
    const from = new Date();
    from.setDate(from.getDate() - 30);
    setDateFrom(fmtDateTimeLocal(from));
    setDateTo(fmtDateTimeLocal(to));
  };

  const updateSearch = (value: string) => {
    setPage(0);
    setSearch(value);
  };

  const updateDateFrom = (value: string) => {
    setPage(0);
    setDateFrom(value);
  };

  const updateDateTo = (value: string) => {
    setPage(0);
    setDateTo(value);
  };

  const updatePageSize = (value: number) => {
    setPage(0);
    setPageSize(value);
  };

  const toggleColumn = (columnId: string) => {
    setHiddenIds((prev) => {
      const next = new Set(prev);
      if (next.has(columnId)) next.delete(columnId);
      else next.add(columnId);
      return next;
    });
  };

  const hasActiveFilters =
    Boolean(search) ||
    Object.values(chipFilters).some((s) => s.size > 0) ||
    Boolean(dateFrom) ||
    Boolean(dateTo);

  // ----- Row selection (Sprint 50) --------------------------------------------
  const selection = selectedKeys ?? new Set<string>();
  const pageKeys = useMemo(
    () => pageRows.map((r) => rowKey(r)),
    [pageRows, rowKey],
  );
  const allOnPageSelected =
    pageKeys.length > 0 && pageKeys.every((k) => selection.has(k));
  const someOnPageSelected =
    !allOnPageSelected && pageKeys.some((k) => selection.has(k));

  const toggleRow = (key: string) => {
    if (!onSelectionChange) return;
    const next = new Set(selection);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onSelectionChange(next);
  };

  const togglePage = () => {
    if (!onSelectionChange) return;
    const next = new Set(selection);
    if (allOnPageSelected) {
      for (const k of pageKeys) next.delete(k);
    } else {
      for (const k of pageKeys) next.add(k);
    }
    onSelectionChange(next);
  };

  const clearSelection = () => {
    if (!onSelectionChange) return;
    onSelectionChange(new Set<string>());
  };

  const leadingColumnCount = (selectable ? 1 : 0) + (expandedRow ? 1 : 0);

  const renderCellContent = (col: DataTableColumn<T>, row: T) => {
    if (col.cell) return col.cell(row);
    const v = col.accessor(row);
    return v == null ? "—" : String(v);
  };

  // ----- Render ---------------------------------------------------------------
  return (
    <div className={`space-y-3 ${className}`}>
      {/* Services-style filter bar: one row of search + multi-select dropdowns. */}
      {!hideToolbar && filterBar && (
        <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-3 shadow-sm">
          <div className="flex flex-wrap items-center gap-3">
            {searchableColumns.length > 0 && (
              <div className="relative min-w-[16rem] flex-1">
                <Search
                  size={15}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
                />
                <Input
                  aria-label="Search"
                  value={search}
                  onChange={(e) => updateSearch(e.target.value)}
                  placeholder={searchPlaceholder}
                  className="h-11 pl-9"
                />
              </div>
            )}
            {chipColumns.map((col) => (
              <FilterDropdown
                key={col.id}
                label={col.label}
                options={col.filterChips!.options}
                selected={[...(chipFilters[col.id] ?? new Set<string>())]}
                onToggle={(value) => toggleChip(col.id, value)}
              />
            ))}
            <div className="relative">
              <Button
                variant="secondary"
                onClick={() => setColumnsMenuOpen((o) => !o)}
                title="Show / hide columns"
                className="h-11"
              >
                <Columns3 size={14} /> Columns
              </Button>
              {columnsMenuOpen && (
                <>
                  <div
                    className="fixed inset-0 z-10"
                    onClick={() => setColumnsMenuOpen(false)}
                  />
                  <div className="absolute right-0 top-full z-20 mt-1 max-h-[60vh] w-56 overflow-y-auto rounded-md border border-border-default bg-bg-panel py-1 shadow-lg">
                    {columns.map((col) => {
                      const checked = !hiddenIds.has(col.id);
                      return (
                        <label
                          key={col.id}
                          className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-bg-hover"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleColumn(col.id)}
                          />
                          <span>{col.label}</span>
                        </label>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
            <div className="ml-auto flex items-center gap-2">
              {hasActiveFilters && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={clearAllFilters}
                  className="h-11"
                  title="Clear search + filters"
                >
                  <X size={14} /> Clear
                </Button>
              )}
              {toolbarRight}
            </div>
          </div>

          {/* Date range (only when a column drives it) */}
          {dateRangeColumn && (
            <div className="mt-3 grid gap-2 sm:grid-cols-[6rem_1fr_auto] sm:items-center">
              <span className="inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
                <CalIcon size={12} />
                {dateRangeColumn.label ?? "Range"}
              </span>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  type="datetime-local"
                  aria-label={`${dateRangeColumn.label ?? "Date range"} from`}
                  value={dateFrom}
                  onChange={(e) => updateDateFrom(e.target.value)}
                  className="max-w-[14rem]"
                />
                <span className="text-xs text-fg-muted">→</span>
                <Input
                  type="datetime-local"
                  aria-label={`${dateRangeColumn.label ?? "Date range"} to`}
                  value={dateTo}
                  onChange={(e) => updateDateTo(e.target.value)}
                  className="max-w-[14rem]"
                />
              </div>
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="sm" onClick={setLast7Days}>
                  7d
                </Button>
                <Button variant="ghost" size="sm" onClick={setLast30Days}>
                  30d
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Toolbar (stacked default layout) */}
      {!hideToolbar && !filterBar && (
      <div className="space-y-3 rounded-lg border border-border-subtle bg-bg-panel p-3 shadow-sm">
        <div className="flex flex-wrap items-end gap-3">
          {searchableColumns.length > 0 && (
            <div className="min-w-[12rem] flex-1">
              <Label htmlFor="dt-search">Search</Label>
              <div className="relative">
                <Search
                  size={14}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
                />
                <Input
                  id="dt-search"
                  value={search}
                  onChange={(e) => updateSearch(e.target.value)}
                  placeholder={searchPlaceholder}
                  className="pl-9"
                />
              </div>
            </div>
          )}

          {/* Column show/hide */}
          <div className="relative">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setColumnsMenuOpen((o) => !o)}
              title="Show / hide columns"
            >
              <Columns3 size={14} /> Columns
            </Button>
            {columnsMenuOpen && (
              <>
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setColumnsMenuOpen(false)}
                />
                <div className="absolute right-0 top-full z-20 mt-1 max-h-[60vh] w-56 overflow-y-auto rounded-md border border-border-default bg-bg-panel py-1 shadow-lg">
                  {columns.map((col) => {
                    const checked = !hiddenIds.has(col.id);
                    return (
                      <label
                        key={col.id}
                        className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-bg-hover"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleColumn(col.id)}
                        />
                        <span>{col.label}</span>
                      </label>
                    );
                  })}
                </div>
              </>
            )}
          </div>

          {hasActiveFilters && (
            <Button
              variant="secondary"
              size="sm"
              onClick={clearAllFilters}
              title="Clear search + filters"
            >
              <X size={14} /> Clear
            </Button>
          )}

          {toolbarRight && (
            <div className="ml-auto flex items-center gap-2">{toolbarRight}</div>
          )}
        </div>

        {/* Date range */}
        {dateRangeColumn && (
          <div className="grid gap-2 sm:grid-cols-[6rem_1fr_auto] sm:items-center">
            <span className="inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
              <CalIcon size={12} />
              {dateRangeColumn.label ?? "Range"}
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                type="datetime-local"
                aria-label={`${dateRangeColumn.label ?? "Date range"} from`}
                value={dateFrom}
                onChange={(e) => updateDateFrom(e.target.value)}
                className="max-w-[14rem]"
              />
              <span className="text-xs text-fg-muted">→</span>
              <Input
                type="datetime-local"
                aria-label={`${dateRangeColumn.label ?? "Date range"} to`}
                value={dateTo}
                onChange={(e) => updateDateTo(e.target.value)}
                className="max-w-[14rem]"
              />
            </div>
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="sm" onClick={setLast7Days}>
                7d
              </Button>
              <Button variant="ghost" size="sm" onClick={setLast30Days}>
                30d
              </Button>
            </div>
          </div>
        )}

        {/* Filter chips */}
        {chipColumns.length > 0 && (
          <div className="space-y-2">
            {chipColumns.map((col) => {
              const active = chipFilters[col.id] ?? new Set<string>();
              return (
                <div
                  key={col.id}
                  className="grid gap-2 sm:grid-cols-[6rem_1fr] sm:items-center"
                >
                  <span className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
                    {col.label}
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {col.filterChips!.options.map((opt) => {
                      const isActive = active.has(opt.value);
                      return (
                        <button
                          key={opt.value}
                          type="button"
                          onClick={() => toggleChip(col.id, opt.value)}
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
                </div>
              );
            })}
          </div>
        )}
      </div>
      )}

      {/* Bulk action bar (only when rows are selected) */}
      {selectable && selection.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-accent bg-accent-bg/40 px-3 py-2 text-sm">
          <span className="font-medium text-fg-primary">
            {selection.size} selected
          </span>
          {bulkActions && (
            <span className="ml-2 inline-flex flex-wrap items-center gap-2">
              {bulkActions(selection, rows)}
            </span>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={clearSelection}
            className="ml-auto"
          >
            <X size={14} /> Clear
          </Button>
        </div>
      )}

      {/* Pagination + table controls */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border-subtle bg-bg-panel px-3 py-1 text-xs text-fg-muted shadow-sm">
        <div className="inline-flex min-h-7 items-center font-medium text-fg-secondary">
          {sortedRows.length === 0
            ? "Showing 0 of 0"
            : `Showing ${firstVisibleRow}-${lastVisibleRow} of ${sortedRows.length}${
                rows.length !== sortedRows.length
                  ? ` (filtered from ${rows.length})`
                  : ""
              }`}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <span className="whitespace-nowrap">Rows per page</span>
          {/* Width comes from the wrapper, not utility overrides on Select —
              conflicting w-full/w-16 and py-2/py-0 utilities resolve by
              stylesheet order and were clipping the value text. */}
          <div className="w-20 shrink-0">
            <Select
              aria-label="Rows per page"
              value={String(pageSize)}
              onChange={(e) => updatePageSize(Number(e.target.value))}
            >
              {pageSizeOptions.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={safePage === 0}
              className="h-7 px-2"
            >
              Previous
            </Button>
            {pageItems.map((item, index) =>
              item === "ellipsis" ? (
                <span
                  key={`ellipsis-${index}`}
                  className="px-1 text-fg-muted"
                  aria-hidden="true"
                >
                  ...
                </span>
              ) : (
                <Button
                  key={item}
                  variant={item === safePage ? "primary" : "ghost"}
                  size="sm"
                  onClick={() => setPage(item)}
                  aria-current={item === safePage ? "page" : undefined}
                  title={`Page ${item + 1}`}
                  className="h-7 min-w-7 px-2"
                >
                  {item + 1}
                </Button>
              ),
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={safePage >= totalPages - 1}
              className="h-7 px-2"
            >
              Next
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPage(totalPages - 1)}
              disabled={safePage >= totalPages - 1}
              className="h-7 px-2"
            >
              Last
            </Button>
          </div>
        </div>
      </div>

      {/* Table */}
      {pageRows.length === 0 ? (
        empty ?? (
          <div className="rounded-lg border border-dashed border-border-strong bg-bg-elevated px-6 py-12 text-center text-sm text-fg-muted">
            {hasActiveFilters
              ? "No rows match the current filters."
              : "No rows yet."}
          </div>
        )
      ) : (
        <>
          <div className="space-y-3 md:hidden">
            {selectable && (
              <div className="flex items-center justify-between rounded-lg border border-border-default bg-bg-panel px-3 py-2 text-sm shadow-sm">
                <label className="inline-flex items-center gap-2 text-fg-primary">
                  <input
                    type="checkbox"
                    checked={allOnPageSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = someOnPageSelected;
                    }}
                    onChange={togglePage}
                    aria-label="Select all rows on this page"
                  />
                  <span>Select page</span>
                </label>
                <span className="text-xs text-fg-muted">
                  {pageRows.length} row{pageRows.length === 1 ? "" : "s"}
                </span>
              </div>
            )}

            {pageRows.map((row) => {
              const k = rowKey(row);
              const isSelected = selection.has(k);
              const isExpanded = Boolean(expandedRow?.expandedKeys.has(k));
              const primaryColumn = visibleColumns[0] ?? null;
              const remainingColumns = visibleColumns.slice(1);
              return (
                <div
                  key={k}
                  className={`rounded-lg border border-border-default bg-bg-panel p-4 shadow-sm ${
                    isSelected ? "border-accent bg-accent-bg/20" : ""
                  } ${isExpanded ? "bg-bg-elevated/60" : ""}`}
                >
                  <div className="space-y-3">
                    <div className="flex items-start gap-3">
                      {selectable && (
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleRow(k)}
                          aria-label="Select row"
                          className="mt-1 shrink-0"
                        />
                      )}
                      <div className="min-w-0 flex-1">
                        {phoneLayout ? (
                          phoneLayout(row)
                        ) : (
                          <div className="space-y-3">
                            {primaryColumn && (
                              <div className="min-w-0">
                                {renderCellContent(primaryColumn, row)}
                              </div>
                            )}
                            {remainingColumns.length > 0 && (
                              <div className="grid gap-3">
                                {remainingColumns.map((col) => (
                                  <div key={col.id} className="min-w-0">
                                    <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
                                      {col.label}
                                    </p>
                                    <div className="mt-1 text-sm text-fg-primary">
                                      {renderCellContent(col, row)}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>

                    {(expandedRow || rowActions) && (
                      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border-subtle pt-3">
                        {expandedRow && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => expandedRow.onToggle(k, row)}
                          >
                            {isExpanded ? (
                              <>
                                <ChevronDown size={14} /> Hide details
                              </>
                            ) : (
                              <>
                                <ChevronRight size={14} /> Details
                              </>
                            )}
                          </Button>
                        )}
                        {rowActions?.(row)}
                      </div>
                    )}

                    {expandedRow && isExpanded && (
                      <div className="border-t border-border-subtle pt-3">
                        {expandedRow.render(row)}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="hidden overflow-x-auto rounded-lg border border-border-default bg-bg-panel shadow-sm md:block">
            <table className="min-w-full divide-y divide-border-subtle text-sm">
              <thead className="bg-bg-elevated text-left">
                <tr>
                  {expandedRow && (
                    <th
                      scope="col"
                      className="w-0 whitespace-nowrap px-3 py-2 text-left"
                    >
                      <span className="sr-only">
                        {expandedRow.label ?? "Expand row"}
                      </span>
                    </th>
                  )}
                  {selectable && (
                    <th
                      scope="col"
                      className="w-0 whitespace-nowrap px-3 py-2 text-left"
                    >
                      <input
                        type="checkbox"
                        checked={allOnPageSelected}
                        ref={(el) => {
                          if (el) el.indeterminate = someOnPageSelected;
                        }}
                        onChange={togglePage}
                        aria-label="Select all rows on this page"
                      />
                    </th>
                  )}
                  {visibleColumns.map((col) => {
                    const isSorted = sortState.columnId === col.id;
                    const align =
                      col.align === "right"
                        ? "text-right"
                        : col.align === "center"
                          ? "text-center"
                          : "text-left";
                    return (
                      <th
                        key={col.id}
                        scope="col"
                        className={`whitespace-nowrap px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-fg-secondary ${align}`}
                      >
                        {col.sortable ? (
                          <button
                            type="button"
                            onClick={() => cycleSort(col.id)}
                            className="inline-flex items-center gap-1 hover:text-fg-primary"
                          >
                            <span>{col.label}</span>
                            {isSorted && sortState.dir === "asc" && (
                              <ArrowUp size={11} />
                            )}
                            {isSorted && sortState.dir === "desc" && (
                              <ArrowDown size={11} />
                            )}
                            {!isSorted && (
                              <ArrowUpDown size={11} className="opacity-40" />
                            )}
                          </button>
                        ) : (
                          <span>{col.label}</span>
                        )}
                      </th>
                    );
                  })}
                  {rowActions && (
                    <th
                      scope="col"
                      className="w-0 whitespace-nowrap px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-fg-secondary text-right"
                    >
                      {/* row actions header */}
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {pageRows.map((row) => {
                  const k = rowKey(row);
                  const isSelected = selection.has(k);
                  const isExpanded = Boolean(expandedRow?.expandedKeys.has(k));
                  return (
                    <Fragment key={k}>
                      <tr
                        className={`hover:bg-bg-hover/40 transition-colors ${
                          isSelected ? "bg-accent-bg/30" : ""
                        } ${isExpanded ? "bg-bg-elevated/60" : ""}`}
                      >
                        {expandedRow && (
                          <td className="w-0 whitespace-nowrap px-3 py-2 align-middle">
                            <button
                              type="button"
                              onClick={() => expandedRow.onToggle(k, row)}
                              className="inline-flex h-6 w-6 items-center justify-center rounded text-fg-muted hover:bg-bg-hover hover:text-fg-primary"
                              aria-label={
                                isExpanded
                                  ? "Collapse row details"
                                  : "Expand row details"
                              }
                            >
                              {isExpanded ? (
                                <ChevronDown size={14} />
                              ) : (
                                <ChevronRight size={14} />
                              )}
                            </button>
                          </td>
                        )}
                        {selectable && (
                          <td className="w-0 whitespace-nowrap px-3 py-2 align-middle">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleRow(k)}
                              aria-label="Select row"
                            />
                          </td>
                        )}
                        {visibleColumns.map((col) => {
                          const align =
                            col.align === "right"
                              ? "text-right"
                              : col.align === "center"
                                ? "text-center"
                                : "text-left";
                          return (
                            <td
                              key={col.id}
                              className={`px-3 py-2 align-middle text-sm text-fg-primary ${align}`}
                            >
                              {renderCellContent(col, row)}
                            </td>
                          );
                        })}
                        {rowActions && (
                          <td className="px-3 py-2 text-right align-middle">
                            {rowActions(row)}
                          </td>
                        )}
                      </tr>
                      {expandedRow && isExpanded && (
                        <tr key={`${k}:expanded`} className="bg-bg-elevated/60">
                          <td
                            colSpan={
                              leadingColumnCount +
                              visibleColumns.length +
                              (rowActions ? 1 : 0)
                            }
                            className="px-4 py-4"
                          >
                            {expandedRow.render(row)}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function _loadPersisted(storageKey: string | undefined): PersistedState {
  if (!storageKey || typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}
