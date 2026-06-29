"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Wrench } from "lucide-react";
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

  // Load rosters + eligible covering users for the day-click create forms.
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
            ? " Click a day to add an override or maintenance window."
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

      <div className="relative rounded-lg border border-border-subtle bg-bg-surface">
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
            return (
              <button
                key={iso}
                type="button"
                onClick={() => day && setSelectedDay(day)}
                className={`min-h-[7rem] border-b border-r border-border-subtle p-1.5 text-left align-top transition-colors hover:bg-bg-elevated ${
                  inMonth ? "" : "opacity-40"
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
                {day?.suppressed ? (
                  <div className="rounded border border-amber-400/40 bg-amber-500/10 px-1.5 py-1 text-[11px] text-amber-200">
                    Maintenance
                  </div>
                ) : (
                  <div className="space-y-1">
                    {(day?.chains ?? []).slice(0, 2).map((chain) => {
                      const l1 = chain.levels[0];
                      const short = l1 ? statusShort(l1.status) : "No levels";
                      return (
                        <div
                          key={chain.chain_id}
                          className={`truncate rounded border px-1.5 py-0.5 text-[11px] ${personColor(
                            l1?.resolved_user_id,
                          )}`}
                          title={`${chain.chain_name} · L1`}
                        >
                          {l1?.resolved_user_name || short || "—"}
                        </div>
                      );
                    })}
                    {(day?.chains.length ?? 0) > 2 && (
                      <div className="px-1 text-[11px] text-fg-muted">
                        +{(day?.chains.length ?? 0) - 2} more
                      </div>
                    )}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>

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
  const dateLabel = new Date(`${day.date}T00:00:00`).toLocaleDateString(
    undefined,
    { weekday: "long", month: "long", day: "numeric", year: "numeric" },
  );
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
            <ChainBlock key={chain.chain_id} chain={chain} />
          ))
        )}

        {canEdit && (
          <DayActions
            day={day}
            teamId={teamId}
            rosters={rosters}
            memberOptions={memberOptions}
            onCreated={onCreated}
          />
        )}
      </div>
    </Modal>
  );
}

// ISO datetime bounds for a whole calendar day (UTC), used as quick defaults
// for an inline override / maintenance window. Operators can fine-tune times in
// the dedicated Rosters / Maintenance Windows forms.
function dayBounds(dateIso: string): { start: string; end: string } {
  const start = new Date(`${dateIso}T00:00:00.000Z`);
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

function DayActions({
  day,
  teamId,
  rosters,
  memberOptions,
  onCreated,
}: {
  day: TeamOnCallCalendarDay;
  teamId: string;
  rosters: RosterResponse[];
  memberOptions: MultiSelectOption[];
  onCreated: () => void;
}) {
  const toast = useToast();
  const [rosterId, setRosterId] = useState("");
  const [coveringUserId, setCoveringUserId] = useState("");
  const [mwName, setMwName] = useState(`Maintenance — ${day.date}`);
  const [busy, setBusy] = useState(false);

  const submitOverride = async () => {
    if (!rosterId || !coveringUserId) {
      toast.error("Pick a roster and a covering person.");
      return;
    }
    setBusy(true);
    try {
      const { start, end } = dayBounds(day.date);
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
      const { start, end } = dayBounds(day.date);
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
    <div className="space-y-4 border-t border-border-subtle pt-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label className="text-xs">Add coverage override</Label>
          <div className="mt-1 space-y-2">
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
            <Button
              variant="secondary"
              onClick={submitOverride}
              disabled={busy}
              className="w-full"
            >
              Add override for {day.date}
            </Button>
          </div>
        </div>
        <div>
          <Label className="text-xs">Add maintenance window</Label>
          <div className="mt-1 space-y-2">
            <Input
              aria-label="Maintenance window name"
              value={mwName}
              onChange={(e) => setMwName(e.target.value)}
            />
            <Button
              variant="secondary"
              onClick={submitMaintenance}
              disabled={busy}
              className="w-full"
            >
              Suppress {day.date} (team)
            </Button>
          </div>
        </div>
      </div>
      <p className="text-[11px] text-fg-muted">
        Quick actions default to the full day. Fine-tune times in Rosters →
        overrides or Maintenance Windows.
      </p>
    </div>
  );
}

function ChainBlock({ chain }: { chain: TeamCalendarChain }) {
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
              <span className="truncate text-xs text-fg-muted">
                {level.resolved_user_email ?? level.target_name}
                {level.coverage_start && level.coverage_end
                  ? ` · ${level.coverage_start}–${level.coverage_end}`
                  : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
