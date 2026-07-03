"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ChevronLeft, ChevronRight, Wrench, X } from "lucide-react";
import {
  createRosterOverride,
  getTeamOnCallCalendar,
  listRosters,
  listTeamMembers,
  listTeams,
  listUsers,
} from "@/lib/api";
import { createMaintenanceWindow } from "@/lib/api_reliability";
import type {
  EscalationCalendarLevel,
  RosterResponse,
  TeamCalendarChain,
  TeamOnCallCalendarDay,
  TeamOnCallCalendarResponse,
  TeamResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { personColor } from "@/lib/calendarColor";
import {
  eligibleRosterMemberOptions,
} from "@/lib/rosterEligibility";
import type { MultiSelectOption } from "@/components/ui/MultiSelect";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const GRID_DAYS = 42; // 6 weeks
const MAX_MONTHS_AHEAD = 12;
// Cap on rendered rows (level chips + chain captions) per day cell.
const MAX_CELL_ROWS = 6;
const MAX_LEVELS_PER_CHAIN = 3;

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function firstOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}

// The Sunday on/before the 1st of the displayed month — top-left grid cell.
function gridStart(month: Date): Date {
  const first = firstOfMonth(month);
  const back = first.getDay(); // 0 = Sunday
  return new Date(first.getFullYear(), first.getMonth(), 1 - back);
}

function monthsBetween(a: Date, b: Date): number {
  return (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth());
}

function statusShort(status: string): string {
  switch (status) {
    case "covered":
      return "";
    case "maintenance":
      return "Maintenance";
    case "outside_coverage":
      return "No coverage now";
    case "empty_roster":
      return "Empty roster";
    case "disabled_roster":
      return "Roster off";
    case "inactive_user":
    case "deleted_user":
      return "Unavailable";
    default:
      return "Gap";
  }
}

/** All ISO dates from a to b inclusive (either order). */
function rangeDates(a: string, b: string): string[] {
  let start = new Date(`${a}T00:00:00`);
  let end = new Date(`${b}T00:00:00`);
  if (start > end) [start, end] = [end, start];
  const out: string[] = [];
  for (let d = start; d <= end; d = new Date(d.getTime() + 86_400_000)) {
    out.push(isoDate(d));
  }
  return out;
}

/** Split sorted ISO dates into runs of consecutive days. */
function contiguousRuns(dates: string[]): string[][] {
  const sorted = [...dates].sort();
  const runs: string[][] = [];
  for (const date of sorted) {
    const last = runs[runs.length - 1];
    if (
      last &&
      new Date(`${date}T00:00:00`).getTime() -
        new Date(`${last[last.length - 1]}T00:00:00`).getTime() ===
        86_400_000
    ) {
      last.push(date);
    } else {
      runs.push([date]);
    }
  }
  return runs;
}

// ISO datetime bounds for a run of whole calendar days (UTC). Quick actions
// default to full days; operators fine-tune times in the dedicated Rosters /
// Maintenance Windows forms.
function runBounds(run: string[]): { start: string; end: string } {
  const start = new Date(`${run[0]}T00:00:00.000Z`);
  const end = new Date(
    new Date(`${run[run.length - 1]}T00:00:00.000Z`).getTime() + 86_400_000,
  );
  return { start: start.toISOString(), end: end.toISOString() };
}

function formatDayList(dates: string[]): string {
  const runs = contiguousRuns(dates);
  return runs
    .map((run) =>
      run.length === 1 ? run[0] : `${run[0]} → ${run[run.length - 1]}`,
    )
    .join(", ");
}

/**
 * What the selected days have in common, driving the "same person → quick
 * override" affordance: if every selected day resolves the same roster-backed
 * person at Level 1 of the same chain, we can pre-fill the override.
 */
function analyzeSelection(
  dates: string[],
  daysByDate: Map<string, TeamOnCallCalendarDay>,
): {
  samePersonName: string | null;
  prefillRosterId: string | null;
} {
  const personIds = new Set<string>();
  const rosterIds = new Set<string>();
  let personName: string | null = null;
  for (const date of dates) {
    const day = daysByDate.get(date);
    if (!day) return { samePersonName: null, prefillRosterId: null };
    const l1 = day.chains[0]?.levels[0];
    if (!l1 || !l1.resolved_user_id) {
      return { samePersonName: null, prefillRosterId: null };
    }
    personIds.add(l1.resolved_user_id);
    personName = l1.resolved_user_name;
    if (l1.target_type === "roster") rosterIds.add(l1.target_id);
  }
  if (personIds.size === 1) {
    return {
      samePersonName: personName,
      prefillRosterId: rosterIds.size === 1 ? [...rosterIds][0] : null,
    };
  }
  return { samePersonName: null, prefillRosterId: null };
}

export default function OnCallSchedulePage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin" || user?.role === "operator";
  const toast = useToast();

  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [teamId, setTeamId] = useState<string>("");
  const [viewMonth, setViewMonth] = useState<Date>(firstOfMonth(new Date()));
  const [data, setData] = useState<TeamOnCallCalendarResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedDay, setSelectedDay] = useState<TeamOnCallCalendarDay | null>(
    null,
  );
  // Edit affordances (admin/operator): the team's rosters + eligible covering
  // users, loaded only when the user can create overrides.
  const [rosters, setRosters] = useState<RosterResponse[]>([]);
  const [memberOptions, setMemberOptions] = useState<MultiSelectOption[]>([]);

  // Multi-day selection (admin/operator): drag across days or Ctrl/Cmd-click
  // to build a selection, then act on it from the floating actions bar.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dragRange, setDragRange] = useState<{ a: string; b: string } | null>(
    null,
  );
  const dragRangeRef = useRef<{ a: string; b: string } | null>(null);
  const dragAnchor = useRef<string | null>(null);
  const dragMoved = useRef(false);
  const dragUnion = useRef(false);
  const [bulkAction, setBulkAction] = useState<null | "override" | "maintenance">(
    null,
  );

  const today = isoDate(new Date());
  const realMonth = firstOfMonth(new Date());
  const canGoNext = monthsBetween(realMonth, viewMonth) < MAX_MONTHS_AHEAD;

  useEffect(() => {
    listTeams()
      .then((res) => {
        setTeams(res.items);
        if (res.items.length > 0) setTeamId((cur) => cur || res.items[0].id);
      })
      .catch((err) =>
        toast.error(err instanceof Error ? err.message : String(err)),
      );
  }, [toast]);

  const refresh = useCallback(async () => {
    if (!teamId) {
      setData(null);
      return;
    }
    setLoading(true);
    try {
      const resp = await getTeamOnCallCalendar(teamId, {
        start: isoDate(gridStart(viewMonth)),
        days: GRID_DAYS,
      });
      setData(resp);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [teamId, viewMonth, toast]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The selection refers to dates resolved against the loaded window — drop it
  // when the team or visible month changes.
  useEffect(() => {
    setSelected(new Set());
    setDragRange(null);
    dragRangeRef.current = null;
    dragAnchor.current = null;
  }, [teamId, viewMonth]);

  // Load rosters + eligible covering users for the create forms.
  useEffect(() => {
    if (!teamId || !canEdit) {
      setRosters([]);
      setMemberOptions([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [rostersRes, usersRes, membersRes] = await Promise.all([
          listRosters(teamId),
          listUsers({ limit: 1000 }),
          listTeamMembers(teamId),
        ]);
        if (cancelled) return;
        setRosters(rostersRes.items);
        const memberIds = new Set(membersRes.items.map((m) => m.user_id));
        setMemberOptions(eligibleRosterMemberOptions(usersRes.items, memberIds));
      } catch (err) {
        if (!cancelled)
          toast.error(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [teamId, canEdit, toast]);

  const daysByDate = useMemo(() => {
    const map = new Map<string, TeamOnCallCalendarDay>();
    for (const day of data?.days ?? []) map.set(day.date, day);
    return map;
  }, [data]);

  const cells = useMemo(() => {
    const start = gridStart(viewMonth);
    return Array.from({ length: GRID_DAYS }, (_, i) => {
      const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
      return { date: d, iso: isoDate(d) };
    });
  }, [viewMonth]);

  // Live preview of the range while dragging.
  const previewSet = useMemo(() => {
    if (!dragRange) return null;
    return new Set(
      rangeDates(dragRange.a, dragRange.b).filter((d) => daysByDate.has(d)),
    );
  }, [dragRange, daysByDate]);

  const commitDrag = useCallback(() => {
    const range = dragRangeRef.current;
    if (range && dragMoved.current) {
      const picked = rangeDates(range.a, range.b).filter((d) =>
        daysByDate.has(d),
      );
      setSelected((prev) => {
        const next = dragUnion.current ? new Set(prev) : new Set<string>();
        for (const d of picked) next.add(d);
        return next;
      });
    }
    dragAnchor.current = null;
    dragMoved.current = false;
    dragRangeRef.current = null;
    setDragRange(null);
  }, [daysByDate]);

  // Finalize a drag even when the pointer is released outside the grid.
  useEffect(() => {
    const up = () => {
      if (dragAnchor.current) commitDrag();
    };
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, [commitDrag]);

  // Esc clears the selection.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(new Set());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onCellPointerDown = (iso: string, e: React.PointerEvent) => {
    if (!canEdit || e.button !== 0) return;
    dragAnchor.current = iso;
    dragMoved.current = false;
    dragUnion.current = e.ctrlKey || e.metaKey;
  };

  const onCellPointerEnter = (iso: string) => {
    if (!dragAnchor.current) return;
    if (iso !== dragAnchor.current) dragMoved.current = true;
    const range = { a: dragAnchor.current, b: iso };
    dragRangeRef.current = range;
    setDragRange(range);
  };

  const onCellPointerUp = (iso: string, e: React.PointerEvent) => {
    const wasDrag = dragAnchor.current && dragMoved.current;
    if (wasDrag) {
      commitDrag();
      return;
    }
    dragAnchor.current = null;
    dragRangeRef.current = null;
    setDragRange(null);
    const day = daysByDate.get(iso);
    if (canEdit && (e.ctrlKey || e.metaKey)) {
      if (!day) return;
      setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(iso)) next.delete(iso);
        else next.add(iso);
        return next;
      });
      return;
    }
    // Plain click: with an active selection it deselects; otherwise it opens
    // the day details.
    if (selected.size > 0) {
      setSelected(new Set());
      return;
    }
    if (day) setSelectedDay(day);
  };

  const selectedDates = useMemo(() => [...selected].sort(), [selected]);
  const selectionInfo = useMemo(
    () => analyzeSelection(selectedDates, daysByDate),
    [selectedDates, daysByDate],
  );

  const monthLabel = viewMonth.toLocaleString(undefined, {
    month: "long",
    year: "numeric",
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-fg-default">On Call Schedule</h1>
        <p className="text-sm text-fg-muted">
          Who is on call for a team across all of its escalation chains, by level.
          {canEdit
            ? " Click a day for details. Drag (or Ctrl-click) days to select them, then act from the bar below."
            : " Read-only."}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Select
          aria-label="Team"
          value={teamId}
          onChange={(e) => setTeamId(e.target.value)}
          className="w-56"
        >
          {teams.length === 0 && <option value="">No teams</option>}
          {teams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </Select>

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={() => setViewMonth((m) => addMonths(m, -1))}
            aria-label="Previous month"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="min-w-[10rem] text-center text-sm font-medium text-fg-default">
            {monthLabel}
          </span>
          <Button
            variant="ghost"
            onClick={() => setViewMonth((m) => addMonths(m, 1))}
            disabled={!canGoNext}
            aria-label="Next month"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            onClick={() => setViewMonth(firstOfMonth(new Date()))}
          >
            Today
          </Button>
        </div>
      </div>

      <div className="relative select-none rounded-lg border border-border-subtle bg-bg-surface">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-bg-surface/60">
            <Spinner />
          </div>
        )}
        <div className="grid grid-cols-7 border-b border-border-subtle">
          {WEEKDAYS.map((w) => (
            <div
              key={w}
              className="px-2 py-1.5 text-center text-xs font-medium text-fg-muted"
            >
              {w}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7">
          {cells.map(({ date, iso }) => {
            const inMonth = date.getMonth() === viewMonth.getMonth();
            const day = daysByDate.get(iso);
            const isToday = iso === today;
            const isSelected = selected.has(iso);
            const isPreview = previewSet?.has(iso) ?? false;
            return (
              <button
                key={iso}
                type="button"
                onPointerDown={(e) => onCellPointerDown(iso, e)}
                onPointerEnter={() => onCellPointerEnter(iso)}
                onPointerUp={(e) => onCellPointerUp(iso, e)}
                className={`min-h-[8.5rem] border-b border-r border-border-subtle p-1.5 text-left align-top transition-colors hover:bg-bg-elevated ${
                  inMonth ? "" : "opacity-40"
                } ${
                  isSelected || isPreview
                    ? "bg-accent/10 ring-2 ring-inset ring-accent"
                    : ""
                }`}
              >
                <div className="mb-1 flex items-center justify-between">
                  <span
                    className={`text-xs ${
                      isToday
                        ? "flex h-5 w-5 items-center justify-center rounded-full bg-accent text-white"
                        : "text-fg-muted"
                    }`}
                  >
                    {date.getDate()}
                  </span>
                  {day?.suppressed && (
                    <Wrench className="h-3 w-3 text-amber-400" />
                  )}
                </div>
                {day && <DayCellContent day={day} />}
              </button>
            );
          })}
        </div>
      </div>

      {canEdit && selected.size > 0 && (
        <div className="sticky bottom-4 z-20 mx-auto flex w-fit flex-wrap items-center gap-3 rounded-full border border-border-subtle bg-bg-elevated px-4 py-2 shadow-lg">
          <span className="text-sm text-fg-default">
            <span className="font-semibold">{selected.size}</span>{" "}
            {selected.size === 1 ? "day" : "days"} selected
            {selectionInfo.samePersonName && (
              <span className="text-fg-muted">
                {" "}
                · all covered by{" "}
                <span className="font-medium text-fg-default">
                  {selectionInfo.samePersonName}
                </span>
              </span>
            )}
          </span>
          <Button variant="secondary" onClick={() => setBulkAction("override")}>
            Override coverage…
          </Button>
          <Button
            variant="secondary"
            onClick={() => setBulkAction("maintenance")}
          >
            Maintenance window…
          </Button>
          <Button
            variant="ghost"
            onClick={() => setSelected(new Set())}
            aria-label="Clear selection"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}

      {selectedDay && (
        <DayDetailModal
          day={selectedDay}
          teamId={teamId}
          teamName={data?.team_name ?? null}
          canEdit={canEdit}
          rosters={rosters}
          memberOptions={memberOptions}
          onClose={() => setSelectedDay(null)}
          onCreated={() => {
            setSelectedDay(null);
            void refresh();
          }}
        />
      )}

      {bulkAction === "override" && (
        <BulkOverrideModal
          dates={selectedDates}
          rosters={rosters}
          memberOptions={memberOptions}
          samePersonName={selectionInfo.samePersonName}
          prefillRosterId={selectionInfo.prefillRosterId}
          onClose={() => setBulkAction(null)}
          onCreated={() => {
            setBulkAction(null);
            setSelected(new Set());
            void refresh();
          }}
        />
      )}

      {bulkAction === "maintenance" && (
        <BulkMaintenanceModal
          dates={selectedDates}
          teamId={teamId}
          teamName={data?.team_name ?? null}
          onClose={() => setBulkAction(null)}
          onCreated={() => {
            setBulkAction(null);
            setSelected(new Set());
            void refresh();
          }}
        />
      )}
    </div>
  );
}

/**
 * Compact per-day cell body: every chain's levels as "L1 · person" chips with
 * the chain name as a caption, capped to keep the cell readable.
 */
function DayCellContent({ day }: { day: TeamOnCallCalendarDay }) {
  if (day.suppressed) {
    return (
      <div className="rounded border border-amber-400/40 bg-amber-500/10 px-1.5 py-1 text-[11px] text-amber-200">
        Maintenance
      </div>
    );
  }
  const rows: React.ReactNode[] = [];
  let rendered = 0;
  let truncated = 0;
  for (const chain of day.chains) {
    const levels = chain.levels.slice(0, MAX_LEVELS_PER_CHAIN);
    const needed = 1 + levels.length; // caption + level rows
    if (rendered + needed > MAX_CELL_ROWS) {
      truncated += 1;
      continue;
    }
    rows.push(
      <div
        key={`${chain.chain_id}-caption`}
        className="truncate px-0.5 text-[10px] font-medium uppercase tracking-wide text-fg-muted"
        title={chain.chain_name}
      >
        {chain.chain_name}
      </div>,
    );
    for (const level of levels) {
      rows.push(
        <div
          key={`${chain.chain_id}-l${level.level}`}
          className={`flex items-center gap-1 truncate rounded border px-1.5 py-0.5 text-[11px] ${personColor(
            level.resolved_user_id,
          )}`}
          title={`${chain.chain_name} · Level ${level.level} · ${
            level.resolved_user_name || statusShort(level.status) || "—"
          }`}
        >
          <span className="shrink-0 font-semibold opacity-70">
            L{level.level}
          </span>
          <span className="truncate">
            {level.resolved_user_name || statusShort(level.status) || "—"}
          </span>
        </div>,
      );
    }
    if (chain.levels.length > MAX_LEVELS_PER_CHAIN) {
      rows.push(
        <div
          key={`${chain.chain_id}-more`}
          className="px-1 text-[10px] text-fg-muted"
        >
          +{chain.levels.length - MAX_LEVELS_PER_CHAIN} more levels
        </div>,
      );
    }
    rendered += needed;
  }
  return (
    <div className="space-y-0.5">
      {rows}
      {truncated > 0 && (
        <div className="px-1 text-[11px] text-fg-muted">
          +{truncated} more {truncated === 1 ? "chain" : "chains"}
        </div>
      )}
    </div>
  );
}

function DayDetailModal({
  day,
  teamId,
  teamName,
  canEdit,
  rosters,
  memberOptions,
  onClose,
  onCreated,
}: {
  day: TeamOnCallCalendarDay;
  teamId: string;
  teamName: string | null;
  canEdit: boolean;
  rosters: RosterResponse[];
  memberOptions: MultiSelectOption[];
  onClose: () => void;
  onCreated: () => void;
}) {
  // Progressive disclosure: the create forms open on demand — either from the
  // action buttons or from a level row's "Override" shortcut (which pre-fills
  // the roster that backs that level).
  const [mode, setMode] = useState<null | "override" | "maintenance">(null);
  const [prefillRosterId, setPrefillRosterId] = useState<string>("");

  const dateLabel = new Date(`${day.date}T00:00:00`).toLocaleDateString(
    undefined,
    { weekday: "long", month: "long", day: "numeric", year: "numeric" },
  );

  const startOverrideFor = (level: EscalationCalendarLevel) => {
    setPrefillRosterId(level.target_type === "roster" ? level.target_id : "");
    setMode("override");
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`${teamName ? `${teamName} — ` : ""}${dateLabel}`}
      maxWidth="max-w-2xl"
    >
      <div className="space-y-4">
        {day.suppressed && (
          <div className="rounded border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
            Paging is suppressed this day by:{" "}
            {day.maintenance.map((m) => m.name).join(", ") || "a maintenance window"}.
          </div>
        )}
        {day.chains.length === 0 ? (
          <p className="text-sm text-fg-muted">
            This team has no escalation chains.
          </p>
        ) : (
          day.chains.map((chain) => (
            <ChainBlock
              key={chain.chain_id}
              chain={chain}
              canEdit={canEdit}
              onOverrideLevel={startOverrideFor}
            />
          ))
        )}

        {canEdit && (
          <DayActions
            day={day}
            teamId={teamId}
            rosters={rosters}
            memberOptions={memberOptions}
            mode={mode}
            onModeChange={setMode}
            prefillRosterId={prefillRosterId}
            onCreated={onCreated}
          />
        )}
      </div>
    </Modal>
  );
}

function DayActions({
  day,
  teamId,
  rosters,
  memberOptions,
  mode,
  onModeChange,
  prefillRosterId,
  onCreated,
}: {
  day: TeamOnCallCalendarDay;
  teamId: string;
  rosters: RosterResponse[];
  memberOptions: MultiSelectOption[];
  mode: null | "override" | "maintenance";
  onModeChange: (mode: null | "override" | "maintenance") => void;
  prefillRosterId: string;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [rosterId, setRosterId] = useState("");
  const [coveringUserId, setCoveringUserId] = useState("");
  const [mwName, setMwName] = useState(`Maintenance — ${day.date}`);
  const [busy, setBusy] = useState(false);

  // A level row's "Override" shortcut pre-picks the roster backing it.
  useEffect(() => {
    if (prefillRosterId) setRosterId(prefillRosterId);
  }, [prefillRosterId]);

  const submitOverride = async () => {
    if (!rosterId || !coveringUserId) {
      toast.error("Pick a roster and a covering person.");
      return;
    }
    setBusy(true);
    try {
      const { start, end } = runBounds([day.date]);
      await createRosterOverride(rosterId, {
        covering_user_id: coveringUserId,
        starts_at: start,
        ends_at: end,
      });
      toast.success("Override created.");
      onCreated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const submitMaintenance = async () => {
    if (!mwName.trim()) {
      toast.error("Name the maintenance window.");
      return;
    }
    setBusy(true);
    try {
      const { start, end } = runBounds([day.date]);
      await createMaintenanceWindow({
        name: mwName.trim(),
        scope_type: "team",
        scope_id: teamId,
        starts_at: start,
        ends_at: end,
      });
      toast.success(
        "Maintenance window created (admin approval may be required before it suppresses paging).",
      );
      onCreated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 border-t border-border-subtle pt-4">
      {mode === null && (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => onModeChange("override")}>
            Add coverage override…
          </Button>
          <Button
            variant="secondary"
            onClick={() => onModeChange("maintenance")}
          >
            Add maintenance window…
          </Button>
        </div>
      )}

      {mode === "override" && (
        <div className="space-y-2">
          <Label className="text-xs">
            Coverage override for {day.date} (full day)
          </Label>
          <div className="grid gap-2 sm:grid-cols-2">
            <Select
              aria-label="Roster"
              value={rosterId}
              onChange={(e) => setRosterId(e.target.value)}
            >
              <option value="">Select roster…</option>
              {rosters.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </Select>
            <Select
              aria-label="Covering person"
              value={coveringUserId}
              onChange={(e) => setCoveringUserId(e.target.value)}
            >
              <option value="">Select person…</option>
              {memberOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex gap-2">
            <Button onClick={submitOverride} disabled={busy}>
              Create override
            </Button>
            <Button variant="ghost" onClick={() => onModeChange(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {mode === "maintenance" && (
        <div className="space-y-2">
          <Label className="text-xs">
            Maintenance window for {day.date} (team-wide, full day)
          </Label>
          <Input
            aria-label="Maintenance window name"
            value={mwName}
            onChange={(e) => setMwName(e.target.value)}
          />
          <div className="flex gap-2">
            <Button onClick={submitMaintenance} disabled={busy}>
              Create window
            </Button>
            <Button variant="ghost" onClick={() => onModeChange(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      <p className="text-[11px] text-fg-muted">
        Quick actions default to the full day. Fine-tune times in Rosters →
        overrides or Maintenance Windows.
      </p>
    </div>
  );
}

function ChainBlock({
  chain,
  canEdit,
  onOverrideLevel,
}: {
  chain: TeamCalendarChain;
  canEdit: boolean;
  onOverrideLevel: (level: EscalationCalendarLevel) => void;
}) {
  return (
    <div className="rounded-lg border border-border-subtle">
      <div className="border-b border-border-subtle px-3 py-2 text-sm font-medium text-fg-default">
        {chain.chain_name}
      </div>
      {chain.levels.length === 0 ? (
        <div className="px-3 py-2 text-sm text-fg-muted">
          No levels — this chain pages no one.
        </div>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {chain.levels.map((level) => (
            <li
              key={level.level}
              className="flex items-center gap-3 px-3 py-2 text-sm"
            >
              <span className="w-12 shrink-0 text-xs text-fg-muted">
                L{level.level}
              </span>
              <span
                className={`shrink-0 rounded border px-2 py-0.5 text-xs ${personColor(
                  level.resolved_user_id,
                )}`}
              >
                {level.resolved_user_name || statusShort(level.status) || "—"}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs text-fg-muted">
                {level.resolved_user_email ?? level.target_name}
                {level.coverage_start && level.coverage_end
                  ? ` · ${level.coverage_start}–${level.coverage_end}`
                  : ""}
              </span>
              {canEdit && level.target_type === "roster" && (
                <Button
                  variant="ghost"
                  className="shrink-0 text-xs"
                  onClick={() => onOverrideLevel(level)}
                >
                  Override
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function BulkOverrideModal({
  dates,
  rosters,
  memberOptions,
  samePersonName,
  prefillRosterId,
  onClose,
  onCreated,
}: {
  dates: string[];
  rosters: RosterResponse[];
  memberOptions: MultiSelectOption[];
  samePersonName: string | null;
  prefillRosterId: string | null;
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [rosterId, setRosterId] = useState(prefillRosterId ?? "");
  const [coveringUserId, setCoveringUserId] = useState("");
  const [busy, setBusy] = useState(false);
  const runs = useMemo(() => contiguousRuns(dates), [dates]);

  const submit = async () => {
    if (!rosterId || !coveringUserId) {
      toast.error("Pick a roster and a covering person.");
      return;
    }
    setBusy(true);
    try {
      for (const run of runs) {
        const { start, end } = runBounds(run);
        await createRosterOverride(rosterId, {
          covering_user_id: coveringUserId,
          starts_at: start,
          ends_at: end,
        });
      }
      toast.success(
        `Created ${runs.length} override${runs.length === 1 ? "" : "s"} covering ${dates.length} day${dates.length === 1 ? "" : "s"}.`,
      );
      onCreated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Override coverage — ${dates.length} day${dates.length === 1 ? "" : "s"}`}
      maxWidth="max-w-lg"
    >
      <div className="space-y-3">
        <p className="text-sm text-fg-muted">{formatDayList(dates)}</p>
        {samePersonName ? (
          <p className="text-sm text-fg-default">
            All selected days are currently covered by{" "}
            <span className="font-medium">{samePersonName}</span>. The override
            replaces them for those days.
          </p>
        ) : (
          <p className="rounded border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
            The selected days are covered by different people — this override
            replaces whoever is scheduled on the chosen roster for each day.
          </p>
        )}
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <Label className="text-xs">Roster</Label>
            <Select
              aria-label="Roster"
              value={rosterId}
              onChange={(e) => setRosterId(e.target.value)}
            >
              <option value="">Select roster…</option>
              {rosters.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label className="text-xs">Covering person</Label>
            <Select
              aria-label="Covering person"
              value={coveringUserId}
              onChange={(e) => setCoveringUserId(e.target.value)}
            >
              <option value="">Select person…</option>
              {memberOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </div>
        </div>
        {runs.length > 1 && (
          <p className="text-[11px] text-fg-muted">
            The selection is non-contiguous — {runs.length} separate overrides
            will be created, one per run of days.
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy}>
            Create override{runs.length === 1 ? "" : "s"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function BulkMaintenanceModal({
  dates,
  teamId,
  teamName,
  onClose,
  onCreated,
}: {
  dates: string[];
  teamId: string;
  teamName: string | null;
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const runs = useMemo(() => contiguousRuns(dates), [dates]);
  const [name, setName] = useState(
    `Maintenance — ${dates[0]}${dates.length > 1 ? ` → ${dates[dates.length - 1]}` : ""}`,
  );
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!name.trim()) {
      toast.error("Name the maintenance window.");
      return;
    }
    setBusy(true);
    try {
      for (let i = 0; i < runs.length; i += 1) {
        const { start, end } = runBounds(runs[i]);
        await createMaintenanceWindow({
          name:
            runs.length === 1 ? name.trim() : `${name.trim()} (${i + 1}/${runs.length})`,
          scope_type: "team",
          scope_id: teamId,
          starts_at: start,
          ends_at: end,
        });
      }
      toast.success(
        `Created ${runs.length} maintenance window${runs.length === 1 ? "" : "s"} (admin approval may be required before paging is suppressed).`,
      );
      onCreated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Maintenance window — ${dates.length} day${dates.length === 1 ? "" : "s"}`}
      maxWidth="max-w-lg"
    >
      <div className="space-y-3">
        <p className="text-sm text-fg-muted">
          {teamName ? `${teamName} · ` : ""}
          {formatDayList(dates)} (full days)
        </p>
        <div>
          <Label className="text-xs">Name</Label>
          <Input
            aria-label="Maintenance window name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        {runs.length > 1 && (
          <p className="text-[11px] text-fg-muted">
            The selection is non-contiguous — {runs.length} separate windows
            will be created, one per run of days.
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy}>
            Create window{runs.length === 1 ? "" : "s"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
