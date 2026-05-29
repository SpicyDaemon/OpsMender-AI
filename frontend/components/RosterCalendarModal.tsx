"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import {
  createRosterOverride,
  deleteRosterOverride,
  listRosterMembers,
  listRosterOverrides,
  listUsers,
  resolveOnCallRange,
} from "@/lib/api";
import type {
  OnCallRangeItem,
  RosterMemberResponse,
  RosterOverrideResponse,
  RosterResponse,
  UserResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { FormError, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";

interface Props {
  roster: RosterResponse;
  onClose: () => void;
  onChange?: () => void;
}

// Deterministic palette so each user gets a stable color across days.
const USER_COLORS = [
  "bg-purple-500/20 border-purple-400 text-purple-100",
  "bg-emerald-500/20 border-emerald-400 text-emerald-100",
  "bg-sky-500/20 border-sky-400 text-sky-100",
  "bg-amber-500/20 border-amber-400 text-amber-100",
  "bg-rose-500/20 border-rose-400 text-rose-100",
  "bg-teal-500/20 border-teal-400 text-teal-100",
  "bg-indigo-500/20 border-indigo-400 text-indigo-100",
  "bg-lime-500/20 border-lime-400 text-lime-100",
  "bg-fuchsia-500/20 border-fuchsia-400 text-fuchsia-100",
  "bg-orange-500/20 border-orange-400 text-orange-100",
];

function hashColor(userId: string | null): string {
  if (!userId) return "bg-bg-elevated border-border-subtle text-fg-muted";
  let hash = 0;
  for (let i = 0; i < userId.length; i += 1) {
    hash = (hash * 31 + userId.charCodeAt(i)) | 0;
  }
  return USER_COLORS[Math.abs(hash) % USER_COLORS.length];
}

function startOfDay(d: Date): Date {
  const out = new Date(d);
  out.setHours(0, 0, 0, 0);
  return out;
}

function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

function fmtIso(d: Date): string {
  return d.toISOString();
}

function fmtDay(d: Date): string {
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

const WINDOW_DAYS = 14;

export function RosterCalendarModal({ roster, onClose, onChange }: Props) {
  const toast = useToast();
  const { user } = useAuth();
  const canEdit = user?.role === "admin" || user?.role === "operator";

  const [windowStart, setWindowStart] = useState<Date>(() => startOfDay(new Date()));
  const [range, setRange] = useState<OnCallRangeItem[]>([]);
  const [overrides, setOverrides] = useState<RosterOverrideResponse[]>([]);
  const [members, setMembers] = useState<RosterMemberResponse[]>([]);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [picking, setPicking] = useState<{ day: Date } | null>(null);

  const windowEnd = useMemo(
    () => addDays(windowStart, WINDOW_DAYS - 1),
    [windowStart],
  );

  // An overnight window wraps past midnight (end at or before start).
  const isOvernight = roster.coverage_end_time <= roster.coverage_start_time;

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [rng, ovs, mbrs, uList] = await Promise.all([
        resolveOnCallRange(roster.id, {
          from: fmtIso(windowStart),
          to: fmtIso(windowEnd),
          step_hours: 24,
        }),
        listRosterOverrides(roster.id),
        listRosterMembers(roster.id),
        listUsers().catch(() => ({ items: [], total: 0 })),
      ]);
      setRange(rng.items);
      setOverrides(ovs.items);
      setMembers(mbrs.items);
      setUsers(uList.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [roster.id, windowStart, windowEnd, toast]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const userNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const u of users) map.set(u.id, u.username);
    return map;
  }, [users]);

  const memberUsers = useMemo(() => {
    return members
      .map((m) => users.find((u) => u.id === m.user_id))
      .filter((u): u is UserResponse => Boolean(u));
  }, [members, users]);

  const deleteOverride = async (overrideId: string) => {
    if (!canEdit) return;
    if (!confirm("Delete this override?")) return;
    try {
      await deleteRosterOverride(roster.id, overrideId);
      toast.success("Override deleted");
      await refresh();
      onChange?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <Modal
      open={true}
      onClose={onClose}
      title={`${roster.name} · calendar`}
      maxWidth="max-w-4xl"
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="info">
              Coverage {roster.coverage_start_time} → {roster.coverage_end_time}
            </Badge>
            {isOvernight && <Badge variant="default">overnight</Badge>}
            <span className="text-xs text-fg-muted">
              {roster.time_zone} · {roster.pattern}
              {roster.pattern === "custom_n_days"
                ? ` (${roster.pattern_length}d)`
                : ""}
            </span>
          </div>
          <span className="text-xs text-fg-muted">
            Each cell shows who holds the coverage window that day.
          </span>
        </div>
        <div className="flex items-center justify-between gap-2 text-sm text-fg-secondary">
          <div>
            <span className="font-medium text-fg-primary">
              {fmtDay(windowStart)} → {fmtDay(windowEnd)}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setWindowStart((d) => addDays(d, -WINDOW_DAYS))}
              title="Previous window"
            >
              <ChevronLeft size={14} />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setWindowStart(startOfDay(new Date()))}
            >
              Today
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setWindowStart((d) => addDays(d, WINDOW_DAYS))}
              title="Next window"
            >
              <ChevronRight size={14} />
            </Button>
          </div>
        </div>

        {loading && range.length === 0 ? (
          <p className="text-sm text-fg-muted">Loading…</p>
        ) : (
          <div className="grid grid-cols-7 gap-1.5">
            {range.map((item) => {
              const d = new Date(item.at);
              const dayLabel = d.toLocaleDateString(undefined, {
                weekday: "short",
              });
              const dateLabel = d.getDate();
              const userName = item.user_id
                ? userNameById.get(item.user_id) ??
                  `${item.user_id.slice(0, 4)}…`
                : "—";
              const colorCls = hashColor(item.user_id);
              return (
                <button
                  key={item.at}
                  type="button"
                  onClick={() => canEdit && setPicking({ day: d })}
                  disabled={!canEdit}
                  className={`flex flex-col gap-1 rounded-md border p-2 text-left transition ${colorCls} ${
                    canEdit
                      ? "hover:ring-1 hover:ring-accent cursor-pointer"
                      : "cursor-default"
                  }`}
                  title={
                    canEdit
                      ? "Click to add an override starting this day"
                      : userName
                  }
                >
                  <div className="flex items-baseline justify-between gap-1">
                    <span className="text-[10px] uppercase tracking-wide opacity-80">
                      {dayLabel}
                    </span>
                    <span className="text-base font-semibold tabular-nums">
                      {dateLabel}
                    </span>
                  </div>
                  <span className="truncate text-xs font-medium">
                    {userName}
                  </span>
                  <span className="text-[10px] tabular-nums opacity-70">
                    {roster.coverage_start_time}–{roster.coverage_end_time}
                  </span>
                  {item.is_override && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-medium">
                      <Sparkles size={9} /> override
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}

        {overrides.length > 0 && (
          <div className="space-y-1.5">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
              Active overrides
            </p>
            <ul className="space-y-1">
              {overrides.map((o) => {
                const u = users.find((x) => x.id === o.covering_user_id);
                return (
                  <li
                    key={o.id}
                    className="flex items-center justify-between gap-2 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-xs"
                  >
                    <div className="min-w-0">
                      <div className="font-medium text-fg-primary">
                        {u?.username ?? o.covering_user_id.slice(0, 8)}
                      </div>
                      <div className="text-fg-muted">
                        {new Date(o.starts_at).toLocaleString()} →{" "}
                        {new Date(o.ends_at).toLocaleString()}
                        {o.reason && (
                          <span className="ml-2 italic">· {o.reason}</span>
                        )}
                      </div>
                    </div>
                    {canEdit && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => deleteOverride(o.id)}
                        title="Delete override"
                      >
                        <Trash2 size={12} />
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        <div className="flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            <X size={14} /> Close
          </Button>
        </div>
      </div>

      {picking && canEdit && (
        <OverrideCreateModal
          rosterId={roster.id}
          defaultStart={picking.day}
          memberUsers={memberUsers}
          onClose={() => setPicking(null)}
          onSaved={async () => {
            setPicking(null);
            await refresh();
            onChange?.();
          }}
        />
      )}
    </Modal>
  );
}

function OverrideCreateModal({
  rosterId,
  defaultStart,
  memberUsers,
  onClose,
  onSaved,
}: {
  rosterId: string;
  defaultStart: Date;
  memberUsers: UserResponse[];
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const toast = useToast();
  const dayIso = (d: Date) => {
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const startOfThatDay = new Date(defaultStart);
  startOfThatDay.setHours(0, 0, 0, 0);
  const endOfThatDay = new Date(defaultStart);
  endOfThatDay.setHours(23, 59, 0, 0);

  const [userId, setUserId] = useState<string>(memberUsers[0]?.id ?? "");
  const [starts, setStarts] = useState<string>(dayIso(startOfThatDay));
  const [ends, setEnds] = useState<string>(dayIso(endOfThatDay));
  const [reason, setReason] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!userId) {
      setError("Pick a covering user");
      return;
    }
    if (new Date(ends) <= new Date(starts)) {
      setError("End time must be after start time");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await createRosterOverride(rosterId, {
        covering_user_id: userId,
        starts_at: new Date(starts).toISOString(),
        ends_at: new Date(ends).toISOString(),
        reason: reason.trim() || undefined,
      });
      toast.success("Override created");
      await onSaved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={true} onClose={onClose} title="Create on-call override">
      <div className="space-y-3">
        <div>
          <Label htmlFor="ov-user">Covering user</Label>
          <Select
            id="ov-user"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
          >
            <option value="">(pick a user)</option>
            {memberUsers.map((u) => (
              <option key={u.id} value={u.id}>
                {u.username}
              </option>
            ))}
          </Select>
          {memberUsers.length === 0 && (
            <p className="mt-1 text-xs text-fg-muted">
              No roster members yet. Add members to the roster first.
            </p>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="ov-start">Starts</Label>
            <Input
              id="ov-start"
              type="datetime-local"
              value={starts}
              onChange={(e) => setStarts(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="ov-end">Ends</Label>
            <Input
              id="ov-end"
              type="datetime-local"
              value={ends}
              onChange={(e) => setEnds(e.target.value)}
            />
          </div>
        </div>
        <div>
          <Label htmlFor="ov-reason">Reason (optional)</Label>
          <Textarea
            id="ov-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            maxLength={500}
            placeholder="e.g. covering a teammate's PTO"
          />
        </div>
        {error && <FormError message={error} />}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? "Creating…" : "Create override"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
