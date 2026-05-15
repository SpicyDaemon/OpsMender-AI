"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Calendar,
  Info,
  ListOrdered,
  PlusCircle,
  Trash2,
  Users,
} from "lucide-react";

import {
  createMaintenanceWindow,
  deleteMaintenanceWindow,
  listMaintenanceWindows,
} from "@/lib/api_reliability";
import {
  addEscalationStep,
  createEscalationChain,
  createPriorityRule,
  createRoster,
  createService,
  createTeam,
  deleteEscalationChain,
  deleteEscalationStep,
  deletePriorityRule,
  deleteRoster,
  deleteService,
  deleteTeam,
  getMyNotificationPreferences,
  listEscalationChains,
  listEscalationSteps,
  listPriorityRules,
  listRosters,
  listServices,
  listTeams,
  updateMyNotificationPreferences,
} from "@/lib/api";
import type {
  EscalationChainResponse,
  EscalationStepResponse,
  EscalationTargetType,
  MaintenanceWindowResponse,
  MaintenanceWindowScopeType,
  NotificationChannelKey,
  Priority,
  PriorityRuleResponse,
  QuietHoursConfig,
  ResponseMode,
  RosterResponse,
  ServiceResponse,
  TeamResponse,
  UserNotificationPrefResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";

type Tab =
  | "teams"
  | "services"
  | "rosters"
  | "rules"
  | "chains"
  | "maintenance"
  | "preferences";

const TABS: { id: Tab; label: string; description: string }[] = [
  { id: "teams", label: "Teams", description: "Org-chart units." },
  {
    id: "services",
    label: "Services",
    description: "Owned by one team in v1.",
  },
  {
    id: "rosters",
    label: "Rosters",
    description: "Deterministic on-call rotations.",
  },
  {
    id: "rules",
    label: "Priority Rules",
    description: "First-match-wins priority assignment.",
  },
  {
    id: "chains",
    label: "Escalation Chains",
    description: "Additive paging steps with timeouts.",
  },
  {
    id: "maintenance",
    label: "Maintenance Windows",
    description: "Suppress paging during planned downtime.",
  },
  {
    id: "preferences",
    label: "My Notifications",
    description: "Channels, routing, and quiet hours.",
  },
];

const PRIORITY_VARIANT: Record<Priority, string> = {
  P0: "critical",
  P1: "high",
  P2: "medium",
  P3: "low",
};

export default function PagingPage() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("teams");
  const [showFlow, setShowFlow] = useState(false);
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [rosters, setRosters] = useState<RosterResponse[]>([]);
  const [rules, setRules] = useState<PriorityRuleResponse[]>([]);
  const [chains, setChains] = useState<EscalationChainResponse[]>([]);
  const [windows, setWindows] = useState<MaintenanceWindowResponse[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [t, s, r, p, c, mw] = await Promise.all([
        listTeams(),
        listServices(),
        listRosters(),
        listPriorityRules(),
        listEscalationChains(),
        listMaintenanceWindows(),
      ]);
      setTeams(t.items);
      setServices(s.items);
      setRosters(r.items);
      setRules(p.items);
      setChains(c.items);
      setWindows(mw.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }, [toast]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="space-y-6 p-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-fg-primary">Paging</h1>
          <p className="text-sm text-fg-secondary">
            Teams, services, rosters, priority rules, and escalation chains —
            OpsMender-owned paging. Maintenance windows and channel fan-out
            land in Sprint 35.
          </p>
        </div>
        <Button
          variant="ghost"
          onClick={() => setShowFlow(true)}
          title="How paging works"
        >
          <Info className="h-4 w-4" /> How it works
        </Button>
      </header>

      <PagingFlowModal open={showFlow} onClose={() => setShowFlow(false)} />

      <nav className="flex flex-wrap gap-1 rounded-md border border-border-default bg-bg-surface p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`flex-1 rounded px-3 py-2 text-left text-sm transition ${
              tab === t.id
                ? "bg-bg-elevated text-fg-primary"
                : "text-fg-secondary hover:text-fg-primary"
            }`}
          >
            <div className="font-medium">{t.label}</div>
            <div className="text-xs text-fg-tertiary">{t.description}</div>
          </button>
        ))}
      </nav>

      {tab === "teams" && <TeamsPanel teams={teams} onChange={refresh} />}
      {tab === "services" && (
        <ServicesPanel
          services={services}
          teams={teams}
          onChange={refresh}
        />
      )}
      {tab === "rosters" && (
        <RostersPanel rosters={rosters} teams={teams} onChange={refresh} />
      )}
      {tab === "rules" && <RulesPanel rules={rules} onChange={refresh} />}
      {tab === "chains" && (
        <ChainsPanel
          chains={chains}
          teams={teams}
          rosters={rosters}
          onChange={refresh}
        />
      )}
      {tab === "maintenance" && (
        <MaintenanceWindowsPanel
          windows={windows}
          services={services}
          rosters={rosters}
          teams={teams}
          onChange={refresh}
        />
      )}
      {tab === "preferences" && <NotificationPreferencesPanel />}
    </div>
  );
}

function TeamsPanel({
  teams,
  onChange,
}: {
  teams: TeamResponse[];
  onChange: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", slug: "", description: "" });

  const submit = async () => {
    if (!form.name || !form.slug) {
      toast.error("Name and slug are required");
      return;
    }
    try {
      await createTeam({
        name: form.name,
        slug: form.slug,
        description: form.description || undefined,
      });
      setOpen(false);
      setForm({ name: "", slug: "", description: "" });
      onChange();
      toast.success("Team created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this team? Services and rosters under it will cascade.")) {
      return;
    }
    try {
      await deleteTeam(id);
      onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex justify-end">
        <Button onClick={() => setOpen(true)}>
          <PlusCircle className="h-4 w-4" /> New team
        </Button>
      </div>
      {teams.length === 0 ? (
        <EmptyState
          title="No teams yet"
          description="Create your first team to start grouping services and rosters."
        />
      ) : (
        <ul className="divide-y divide-border-default rounded-lg border border-border-default bg-bg-surface">
          {teams.map((t) => (
            <li
              key={t.id}
              className="flex items-center justify-between px-4 py-3"
            >
              <div>
                <div className="flex items-center gap-2">
                  <Users className="h-4 w-4 text-fg-secondary" />
                  <span className="font-medium text-fg-primary">{t.name}</span>
                  <Badge variant="default">{t.slug}</Badge>
                </div>
                {t.description && (
                  <div className="text-xs text-fg-secondary">
                    {t.description}
                  </div>
                )}
              </div>
              <Button variant="ghost" onClick={() => remove(t.id)} title="Delete">
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New team">
        <div className="space-y-3">
          <div>
            <Label>Name</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div>
            <Label>Slug (lowercase, hyphens)</Label>
            <Input
              value={form.slug}
              onChange={(e) => setForm({ ...form, slug: e.target.value })}
              placeholder="payments-team"
            />
          </div>
          <div>
            <Label>Description (optional)</Label>
            <Textarea
              value={form.description}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
              rows={2}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>Create</Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}

function ServicesPanel({
  services,
  teams,
  onChange,
}: {
  services: ServiceResponse[];
  teams: TeamResponse[];
  onChange: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: "",
    slug: "",
    team_id: "",
    description: "",
  });

  useEffect(() => {
    if (teams.length > 0 && !form.team_id) {
      setForm((f) => ({ ...f, team_id: teams[0].id }));
    }
  }, [teams, form.team_id]);

  const submit = async () => {
    if (!form.name || !form.slug || !form.team_id) {
      toast.error("Name, slug, and team are required");
      return;
    }
    try {
      await createService({
        team_id: form.team_id,
        name: form.name,
        slug: form.slug,
        description: form.description || undefined,
      });
      setOpen(false);
      setForm({ name: "", slug: "", team_id: teams[0]?.id ?? "", description: "" });
      onChange();
      toast.success("Service created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this service?")) return;
    try {
      await deleteService(id);
      onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex justify-end">
        <Button onClick={() => setOpen(true)} disabled={teams.length === 0}>
          <PlusCircle className="h-4 w-4" /> New service
        </Button>
      </div>
      {services.length === 0 ? (
        <EmptyState
          title="No services yet"
          description={
            teams.length === 0
              ? "Create a team first, then add services to it."
              : "Add a service so incidents can be routed to its owning team."
          }
        />
      ) : (
        <ul className="divide-y divide-border-default rounded-lg border border-border-default bg-bg-surface">
          {services.map((s) => {
            const team = teams.find((t) => t.id === s.team_id);
            return (
              <li
                key={s.id}
                className="flex items-center justify-between px-4 py-3"
              >
                <div>
                  <div className="font-medium text-fg-primary">{s.name}</div>
                  <div className="text-xs text-fg-secondary">
                    {s.slug} · team {team?.name ?? "(unknown)"}
                    {!s.is_active && " · inactive"}
                  </div>
                </div>
                <Button variant="ghost" onClick={() => remove(s.id)} title="Delete">
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            );
          })}
        </ul>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New service">
        <div className="space-y-3">
          <div>
            <Label>Owning team</Label>
            <Select
              value={form.team_id}
              onChange={(e) => setForm({ ...form, team_id: e.target.value })}
            >
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Name</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div>
            <Label>Slug</Label>
            <Input
              value={form.slug}
              onChange={(e) => setForm({ ...form, slug: e.target.value })}
              placeholder="payments-api"
            />
          </div>
          <div>
            <Label>Description (optional)</Label>
            <Textarea
              value={form.description}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
              rows={2}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>Create</Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}

function RostersPanel({
  rosters,
  teams,
  onChange,
}: {
  rosters: RosterResponse[];
  teams: TeamResponse[];
  onChange: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: "",
    team_id: "",
    time_zone: "UTC",
    pattern: "weekly" as "weekly" | "daily" | "custom_n_days",
    pattern_length: 7,
    handoff_time: "09:00",
    anchor_date: new Date().toISOString().slice(0, 10),
  });

  useEffect(() => {
    if (teams.length > 0 && !form.team_id) {
      setForm((f) => ({ ...f, team_id: teams[0].id }));
    }
  }, [teams, form.team_id]);

  const submit = async () => {
    if (!form.name || !form.team_id) {
      toast.error("Name and team required");
      return;
    }
    try {
      await createRoster({
        team_id: form.team_id,
        name: form.name,
        time_zone: form.time_zone,
        pattern: form.pattern,
        pattern_length: form.pattern_length,
        handoff_time: form.handoff_time,
        anchor_date: form.anchor_date,
      });
      setOpen(false);
      onChange();
      toast.success("Roster created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this roster? Members and overrides cascade.")) return;
    try {
      await deleteRoster(id);
      onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex justify-end">
        <Button onClick={() => setOpen(true)} disabled={teams.length === 0}>
          <PlusCircle className="h-4 w-4" /> New roster
        </Button>
      </div>
      {rosters.length === 0 ? (
        <EmptyState
          title="No rosters yet"
          description="Rosters define who is on call. Add one per team to start."
        />
      ) : (
        <ul className="divide-y divide-border-default rounded-lg border border-border-default bg-bg-surface">
          {rosters.map((r) => {
            const team = teams.find((t) => t.id === r.team_id);
            return (
              <li
                key={r.id}
                className="flex items-center justify-between px-4 py-3"
              >
                <div>
                  <div className="flex items-center gap-2">
                    <Calendar className="h-4 w-4 text-fg-secondary" />
                    <span className="font-medium text-fg-primary">{r.name}</span>
                    <Badge variant="default">{r.pattern}</Badge>
                  </div>
                  <div className="text-xs text-fg-secondary">
                    team {team?.name ?? "?"} · {r.time_zone} · handoff{" "}
                    {r.handoff_time}
                  </div>
                </div>
                <Button variant="ghost" onClick={() => remove(r.id)} title="Delete">
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            );
          })}
        </ul>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New roster">
        <div className="space-y-3">
          <div>
            <Label>Team</Label>
            <Select
              value={form.team_id}
              onChange={(e) => setForm({ ...form, team_id: e.target.value })}
            >
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Name</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Primary on-call"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Pattern</Label>
              <Select
                value={form.pattern}
                onChange={(e) =>
                  setForm({ ...form, pattern: e.target.value as never })
                }
              >
                <option value="weekly">Weekly</option>
                <option value="daily">Daily</option>
                <option value="custom_n_days">Custom (N days)</option>
              </Select>
            </div>
            <div>
              <Label>Pattern length (days)</Label>
              <Input
                type="number"
                min={1}
                value={form.pattern_length}
                onChange={(e) =>
                  setForm({
                    ...form,
                    pattern_length: Number(e.target.value) || 1,
                  })
                }
              />
            </div>
            <div>
              <Label>Time zone (IANA)</Label>
              <Input
                value={form.time_zone}
                onChange={(e) =>
                  setForm({ ...form, time_zone: e.target.value })
                }
                placeholder="America/Chicago"
              />
            </div>
            <div>
              <Label>Handoff time</Label>
              <Input
                value={form.handoff_time}
                onChange={(e) =>
                  setForm({ ...form, handoff_time: e.target.value })
                }
                placeholder="09:00"
              />
            </div>
            <div className="col-span-2">
              <Label>Anchor date</Label>
              <Input
                type="date"
                value={form.anchor_date}
                onChange={(e) =>
                  setForm({ ...form, anchor_date: e.target.value })
                }
              />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>Create</Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}

function RulesPanel({
  rules,
  onChange,
}: {
  rules: PriorityRuleResponse[];
  onChange: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: "",
    rule_index: 0,
    priority: "P2" as Priority,
    response_mode: "" as ResponseMode | "",
    condition_json: "{}",
  });

  const submit = async () => {
    let condition: Record<string, unknown> = {};
    try {
      condition = JSON.parse(form.condition_json);
    } catch {
      toast.error("Condition must be valid JSON");
      return;
    }
    try {
      await createPriorityRule({
        name: form.name,
        rule_index: form.rule_index,
        condition,
        priority: form.priority,
        response_mode: form.response_mode || undefined,
      });
      setOpen(false);
      onChange();
      toast.success("Rule created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this rule?")) return;
    try {
      await deletePriorityRule(id);
      onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex justify-end">
        <Button onClick={() => setOpen(true)}>
          <PlusCircle className="h-4 w-4" /> New rule
        </Button>
      </div>
      {rules.length === 0 ? (
        <EmptyState
          title="No priority rules"
          description="Without rules every incident lands at P3. Add a rule to surface real urgencies."
        />
      ) : (
        <ul className="divide-y divide-border-default rounded-lg border border-border-default bg-bg-surface">
          {rules.map((r) => (
            <li
              key={r.id}
              className="flex items-center justify-between px-4 py-3"
            >
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <ListOrdered className="h-4 w-4 text-fg-secondary" />
                  <span className="font-medium text-fg-primary">{r.name}</span>
                  <Badge variant={PRIORITY_VARIANT[r.priority] as never}>
                    {r.priority}
                  </Badge>
                  {r.response_mode && (
                    <Badge variant="default">{r.response_mode}</Badge>
                  )}
                  <span className="text-xs text-fg-tertiary">#{r.rule_index}</span>
                </div>
                <pre className="overflow-x-auto rounded bg-bg-elevated p-2 text-xs text-fg-secondary">
                  {JSON.stringify(r.condition, null, 0)}
                </pre>
              </div>
              <Button variant="ghost" onClick={() => remove(r.id)} title="Delete">
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New priority rule">
        <div className="space-y-3">
          <div>
            <Label>Name</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Critical payments alerts"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Rule index (lower = first)</Label>
              <Input
                type="number"
                min={0}
                value={form.rule_index}
                onChange={(e) =>
                  setForm({ ...form, rule_index: Number(e.target.value) || 0 })
                }
              />
            </div>
            <div>
              <Label>Priority</Label>
              <Select
                value={form.priority}
                onChange={(e) =>
                  setForm({ ...form, priority: e.target.value as Priority })
                }
              >
                <option value="P0">P0</option>
                <option value="P1">P1</option>
                <option value="P2">P2</option>
                <option value="P3">P3</option>
              </Select>
            </div>
            <div className="col-span-2">
              <Label>Response mode (optional override)</Label>
              <Select
                value={form.response_mode}
                onChange={(e) =>
                  setForm({
                    ...form,
                    response_mode: e.target.value as ResponseMode | "",
                  })
                }
              >
                <option value="">Use default for priority</option>
                <option value="auto_resolve">auto_resolve</option>
                <option value="notify">notify</option>
                <option value="page">page</option>
                <option value="escalate_immediate">escalate_immediate</option>
              </Select>
            </div>
            <div className="col-span-2">
              <Label>Match condition (JSON)</Label>
              <Textarea
                value={form.condition_json}
                onChange={(e) =>
                  setForm({ ...form, condition_json: e.target.value })
                }
                rows={5}
                placeholder='{"severity": ["critical"], "external_source": ["datadog"]}'
              />
              <p className="mt-1 text-xs text-fg-tertiary">
                Keys are payload fields (severity, external_source, title, description…). Values are scalars or lists; list semantics is OR. Case-insensitive.
              </p>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>Create</Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}

function ChainsPanel({
  chains,
  teams,
  rosters,
  onChange,
}: {
  chains: EscalationChainResponse[];
  teams: TeamResponse[];
  rosters: RosterResponse[];
  onChange: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", team_id: "", description: "" });
  const [expanded, setExpanded] = useState<string | null>(null);
  const [stepsByChain, setStepsByChain] = useState<
    Record<string, EscalationStepResponse[]>
  >({});

  useEffect(() => {
    if (teams.length > 0 && !form.team_id) {
      setForm((f) => ({ ...f, team_id: teams[0].id }));
    }
  }, [teams, form.team_id]);

  const submit = async () => {
    if (!form.name || !form.team_id) {
      toast.error("Name and team are required");
      return;
    }
    try {
      await createEscalationChain({
        team_id: form.team_id,
        name: form.name,
        description: form.description || undefined,
      });
      setOpen(false);
      setForm({ name: "", team_id: teams[0]?.id ?? "", description: "" });
      onChange();
      toast.success("Chain created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this chain? Steps cascade.")) return;
    try {
      await deleteEscalationChain(id);
      onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const toggle = async (chainId: string) => {
    if (expanded === chainId) {
      setExpanded(null);
      return;
    }
    try {
      const resp = await listEscalationSteps(chainId);
      setStepsByChain({ ...stepsByChain, [chainId]: resp.items });
      setExpanded(chainId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex justify-end">
        <Button onClick={() => setOpen(true)} disabled={teams.length === 0}>
          <PlusCircle className="h-4 w-4" /> New chain
        </Button>
      </div>
      {chains.length === 0 ? (
        <EmptyState
          title="No escalation chains"
          description="Without a chain, incidents flagged for paging do not page anyone. Create one per team."
        />
      ) : (
        <ul className="divide-y divide-border-default rounded-lg border border-border-default bg-bg-surface">
          {chains.map((c) => {
            const team = teams.find((t) => t.id === c.team_id);
            const isOpen = expanded === c.id;
            const steps = stepsByChain[c.id] ?? [];
            return (
              <li key={c.id} className="px-4 py-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <ListOrdered className="h-4 w-4 text-fg-secondary" />
                      <span className="font-medium text-fg-primary">{c.name}</span>
                      {!c.is_active && <Badge variant="default">inactive</Badge>}
                    </div>
                    <div className="text-xs text-fg-secondary">
                      team {team?.name ?? "?"}
                      {c.description ? ` · ${c.description}` : ""}
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <Button variant="ghost" onClick={() => toggle(c.id)}>
                      {isOpen ? "Hide steps" : "Steps"}
                    </Button>
                    <Button variant="ghost" onClick={() => remove(c.id)} title="Delete">
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                {isOpen && (
                  <StepsEditor
                    chainId={c.id}
                    steps={steps}
                    rosters={rosters}
                    teams={teams}
                    onChange={async () => {
                      const resp = await listEscalationSteps(c.id);
                      setStepsByChain({ ...stepsByChain, [c.id]: resp.items });
                    }}
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New escalation chain">
        <div className="space-y-3">
          <div>
            <Label>Team</Label>
            <Select
              value={form.team_id}
              onChange={(e) => setForm({ ...form, team_id: e.target.value })}
            >
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Name</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Primary on-call escalation"
            />
          </div>
          <div>
            <Label>Description (optional)</Label>
            <Textarea
              value={form.description}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
              rows={2}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>Create</Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}

function StepsEditor({
  chainId,
  steps,
  rosters,
  teams,
  onChange,
}: {
  chainId: string;
  steps: EscalationStepResponse[];
  rosters: RosterResponse[];
  teams: TeamResponse[];
  onChange: () => Promise<void>;
}) {
  const toast = useToast();
  const [stepForm, setStepForm] = useState({
    target_type: "roster" as EscalationTargetType,
    target_id: "",
    timeout_seconds: 300,
  });

  const addStep = async () => {
    if (!stepForm.target_id) {
      toast.error("Pick a target");
      return;
    }
    try {
      const nextIndex = steps.length === 0
        ? 0
        : Math.max(...steps.map((s) => s.step_index)) + 1;
      await addEscalationStep(chainId, {
        step_index: nextIndex,
        target_type: stepForm.target_type,
        target_id: stepForm.target_id,
        timeout_seconds: stepForm.timeout_seconds,
      });
      setStepForm({ ...stepForm, target_id: "" });
      await onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const removeStep = async (id: string) => {
    try {
      await deleteEscalationStep(chainId, id);
      await onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const targetOptions =
    stepForm.target_type === "roster" ? rosters :
    stepForm.target_type === "team" ? teams :
    [];

  return (
    <div className="mt-3 space-y-2 rounded border border-border-default bg-bg-elevated p-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
        Steps (additive — once paged, stay paged)
      </div>
      {steps.length === 0 ? (
        <div className="text-xs text-fg-tertiary">No steps yet.</div>
      ) : (
        <ol className="space-y-1">
          {steps.map((s) => {
            const label =
              s.target_type === "roster"
                ? rosters.find((r) => r.id === s.target_id)?.name
                : s.target_type === "team"
                ? teams.find((t) => t.id === s.target_id)?.name
                : s.target_id;
            return (
              <li
                key={s.id}
                className="flex items-center justify-between text-sm"
              >
                <span>
                  <Badge variant="default">#{s.step_index}</Badge>{" "}
                  <span className="text-fg-secondary">{s.target_type}</span>{" "}
                  <span className="text-fg-primary">{label}</span>{" "}
                  <span className="text-fg-tertiary">
                    · {s.timeout_seconds}s
                  </span>
                </span>
                <Button variant="ghost" onClick={() => removeStep(s.id)}>
                  <Trash2 className="h-3 w-3" />
                </Button>
              </li>
            );
          })}
        </ol>
      )}
      <div className="grid grid-cols-[120px_1fr_120px_auto] items-end gap-2 pt-2">
        <div>
          <Label>Type</Label>
          <Select
            value={stepForm.target_type}
            onChange={(e) =>
              setStepForm({
                ...stepForm,
                target_type: e.target.value as EscalationTargetType,
                target_id: "",
              })
            }
          >
            <option value="roster">Roster</option>
            <option value="team">Team</option>
          </Select>
        </div>
        <div>
          <Label>Target</Label>
          <Select
            value={stepForm.target_id}
            onChange={(e) =>
              setStepForm({ ...stepForm, target_id: e.target.value })
            }
          >
            <option value="">— pick —</option>
            {targetOptions.map((o) => (
              <option key={o.id} value={o.id}>
                {(o as { name?: string }).name ?? o.id}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label>Timeout (s)</Label>
          <Input
            type="number"
            min={10}
            value={stepForm.timeout_seconds}
            onChange={(e) =>
              setStepForm({
                ...stepForm,
                timeout_seconds: Number(e.target.value) || 300,
              })
            }
          />
        </div>
        <Button onClick={addStep}>Add step</Button>
      </div>
    </div>
  );
}

function PagingFlowModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="How paging works">
      <div className="space-y-4">
        <p className="text-sm text-fg-secondary">
          Every incident flows through the same pipeline. The first stop decides
          how urgent it is. The second decides whether to wake anyone up. The
          third decides who.
        </p>

        <div className="overflow-x-auto rounded-lg border border-border-default bg-bg-elevated p-4">
          <svg
            viewBox="0 0 720 360"
            className="w-full"
            xmlns="http://www.w3.org/2000/svg"
            role="img"
            aria-label="Paging pipeline diagram"
          >
            <defs>
              <marker
                id="arrow"
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto"
              >
                <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
              </marker>
            </defs>

            {/* row 1: inbound -> priority rules -> response mode */}
            <g className="text-fg-secondary">
              <rect x="10" y="20" width="150" height="48" rx="6" fill="none" stroke="currentColor" />
              <text x="85" y="42" textAnchor="middle" fontSize="12" fill="currentColor" fontWeight="600">
                Inbound alert
              </text>
              <text x="85" y="58" textAnchor="middle" fontSize="10" fill="currentColor">
                (Datadog / Prom / …)
              </text>

              <line x1="160" y1="44" x2="220" y2="44" stroke="currentColor" markerEnd="url(#arrow)" />

              <rect x="220" y="20" width="170" height="48" rx="6" fill="none" stroke="currentColor" />
              <text x="305" y="42" textAnchor="middle" fontSize="12" fill="currentColor" fontWeight="600">
                Priority Rules
              </text>
              <text x="305" y="58" textAnchor="middle" fontSize="10" fill="currentColor">
                first match → P0..P3
              </text>

              <line x1="390" y1="44" x2="450" y2="44" stroke="currentColor" markerEnd="url(#arrow)" />

              <rect x="450" y="20" width="200" height="48" rx="6" fill="none" stroke="currentColor" />
              <text x="550" y="42" textAnchor="middle" fontSize="12" fill="currentColor" fontWeight="600">
                Response Mode
              </text>
              <text x="550" y="58" textAnchor="middle" fontSize="10" fill="currentColor">
                auto_resolve / notify / page
              </text>
            </g>

            {/* row 2: page → service → chain */}
            <g className="text-fg-secondary">
              <line x1="550" y1="68" x2="550" y2="100" stroke="currentColor" markerEnd="url(#arrow)" />

              <rect x="450" y="100" width="200" height="48" rx="6" fill="none" stroke="currentColor" />
              <text x="550" y="122" textAnchor="middle" fontSize="12" fill="currentColor" fontWeight="600">
                Service
              </text>
              <text x="550" y="138" textAnchor="middle" fontSize="10" fill="currentColor">
                owns a Team
              </text>

              <line x1="450" y1="124" x2="390" y2="124" stroke="currentColor" markerEnd="url(#arrow)" />

              <rect x="220" y="100" width="170" height="48" rx="6" fill="none" stroke="currentColor" />
              <text x="305" y="122" textAnchor="middle" fontSize="12" fill="currentColor" fontWeight="600">
                Escalation Chain
              </text>
              <text x="305" y="138" textAnchor="middle" fontSize="10" fill="currentColor">
                ordered steps
              </text>
            </g>

            {/* row 3: chain steps → roster → user */}
            <g className="text-fg-secondary">
              <line x1="305" y1="148" x2="305" y2="180" stroke="currentColor" markerEnd="url(#arrow)" />

              <rect x="180" y="180" width="250" height="60" rx="6" fill="none" stroke="currentColor" />
              <text x="305" y="200" textAnchor="middle" fontSize="12" fill="currentColor" fontWeight="600">
                Step N → target
              </text>
              <text x="305" y="216" textAnchor="middle" fontSize="10" fill="currentColor">
                roster · user · team
              </text>
              <text x="305" y="230" textAnchor="middle" fontSize="10" fill="currentColor">
                timeout → fire next step (additive)
              </text>

              <line x1="430" y1="210" x2="490" y2="210" stroke="currentColor" markerEnd="url(#arrow)" />

              <rect x="490" y="180" width="200" height="60" rx="6" fill="none" stroke="currentColor" />
              <text x="590" y="200" textAnchor="middle" fontSize="12" fill="currentColor" fontWeight="600">
                Roster · on_call_at(now)
              </text>
              <text x="590" y="216" textAnchor="middle" fontSize="10" fill="currentColor">
                weekly / daily / N-day rotation
              </text>
              <text x="590" y="230" textAnchor="middle" fontSize="10" fill="currentColor">
                overrides win
              </text>
            </g>

            {/* row 4: ack / takeover */}
            <g className="text-fg-secondary">
              <line x1="305" y1="240" x2="305" y2="272" stroke="currentColor" markerEnd="url(#arrow)" />

              <rect x="180" y="272" width="250" height="60" rx="6" fill="none" stroke="currentColor" />
              <text x="305" y="292" textAnchor="middle" fontSize="12" fill="currentColor" fontWeight="600">
                Ack → assignee
              </text>
              <text x="305" y="308" textAnchor="middle" fontSize="10" fill="currentColor">
                chain pauses · incident-scoped authority
              </text>
              <text x="305" y="322" textAnchor="middle" fontSize="10" fill="currentColor">
                soft-takeover 5 min · admin force any time
              </text>

              <line x1="430" y1="302" x2="490" y2="302" stroke="currentColor" markerEnd="url(#arrow)" />

              <rect x="490" y="272" width="200" height="60" rx="6" fill="none" stroke="currentColor" />
              <text x="590" y="292" textAnchor="middle" fontSize="12" fill="currentColor" fontWeight="600">
                15-min hard timeout
              </text>
              <text x="590" y="308" textAnchor="middle" fontSize="10" fill="currentColor">
                chain → exhausted
              </text>
              <text x="590" y="322" textAnchor="middle" fontSize="10" fill="currentColor">
                team channel fan-out (S35)
              </text>
            </g>
          </svg>
        </div>

        <ul className="space-y-1 text-xs text-fg-secondary">
          <li>
            <span className="font-semibold text-fg-primary">Priority rules</span>{" "}
            are first-match-wins; LLM escalation can only bump priority up.
          </li>
          <li>
            <span className="font-semibold text-fg-primary">Response mode</span>{" "}
            is locked at incident creation. The AI may resolve an incident but
            never downgrades its mode.
          </li>
          <li>
            <span className="font-semibold text-fg-primary">
              Escalation is additive
            </span>
            : once paged, you stay paged. Later steps add people, they do not
            replace earlier ones.
          </li>
          <li>
            <span className="font-semibold text-fg-primary">
              Incident-scoped authority
            </span>
            : the active assignee gets operator-equivalent rights on that
            incident regardless of global role.
          </li>
        </ul>

        <div className="flex justify-end">
          <Button onClick={onClose}>Got it</Button>
        </div>
      </div>
    </Modal>
  );
}

function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatRange(starts: string, ends: string): string {
  const s = new Date(starts);
  const e = new Date(ends);
  return `${s.toLocaleString()} → ${e.toLocaleString()}`;
}

function MaintenanceWindowsPanel({
  windows,
  services,
  rosters,
  teams,
  onChange,
}: {
  windows: MaintenanceWindowResponse[];
  services: ServiceResponse[];
  rosters: RosterResponse[];
  teams: TeamResponse[];
  onChange: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"active" | "scheduled" | "past">(
    "scheduled",
  );
  const [rangeFrom, setRangeFrom] = useState("");
  const [rangeTo, setRangeTo] = useState("");
  const nowMinusOne = () => {
    const d = new Date();
    d.setHours(d.getHours() + 1);
    return toLocalInput(d.toISOString());
  };
  const nowPlusTwo = () => {
    const d = new Date();
    d.setHours(d.getHours() + 2);
    return toLocalInput(d.toISOString());
  };
  const [form, setForm] = useState<{
    name: string;
    description: string;
    reason: string;
    starts_at: string;
    ends_at: string;
    scope_type: MaintenanceWindowScopeType;
    scope_id: string;
  }>({
    name: "",
    description: "",
    reason: "",
    starts_at: nowMinusOne(),
    ends_at: nowPlusTwo(),
    scope_type: "global",
    scope_id: "",
  });

  const now = new Date();
  const isActive = (w: MaintenanceWindowResponse) =>
    new Date(w.starts_at) <= now && new Date(w.ends_at) > now;
  const isScheduled = (w: MaintenanceWindowResponse) =>
    new Date(w.starts_at) > now;
  const isPast = (w: MaintenanceWindowResponse) =>
    new Date(w.ends_at) <= now;

  const inRange = (w: MaintenanceWindowResponse) => {
    if (rangeFrom) {
      const from = new Date(rangeFrom);
      if (new Date(w.ends_at) < from) return false;
    }
    if (rangeTo) {
      const to = new Date(rangeTo);
      if (new Date(w.starts_at) > to) return false;
    }
    return true;
  };

  const filtered = windows.filter((w) => {
    if (!inRange(w)) return false;
    if (view === "active") return isActive(w);
    if (view === "scheduled") return isScheduled(w);
    return isPast(w);
  });

  const scopeOptionsFor = (
    type: MaintenanceWindowScopeType,
  ): { id: string; name: string }[] => {
    if (type === "service") return services.map((s) => ({ id: s.id, name: s.name }));
    if (type === "roster") return rosters.map((r) => ({ id: r.id, name: r.name }));
    if (type === "team") return teams.map((t) => ({ id: t.id, name: t.name }));
    return [];
  };

  const scopeLabelFor = (w: MaintenanceWindowResponse): string => {
    if (w.scope_type === "global") return "global";
    const opts = scopeOptionsFor(w.scope_type);
    const match = opts.find((o) => o.id === w.scope_id);
    return `${w.scope_type}: ${match?.name ?? w.scope_id ?? "?"}`;
  };

  const submit = async () => {
    if (!form.name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (!form.starts_at || !form.ends_at) {
      toast.error("Start and end times are required");
      return;
    }
    if (new Date(form.ends_at) <= new Date(form.starts_at)) {
      toast.error("End must be after start");
      return;
    }
    if (form.scope_type !== "global" && !form.scope_id) {
      toast.error("Pick a scope target");
      return;
    }
    try {
      await createMaintenanceWindow({
        name: form.name.trim(),
        description: form.description.trim() || null,
        reason: form.reason.trim() || null,
        starts_at: new Date(form.starts_at).toISOString(),
        ends_at: new Date(form.ends_at).toISOString(),
        scope_type: form.scope_type,
        scope_id: form.scope_type === "global" ? null : form.scope_id,
      });
      setOpen(false);
      setForm({
        name: "",
        description: "",
        reason: "",
        starts_at: nowMinusOne(),
        ends_at: nowPlusTwo(),
        scope_type: "global",
        scope_id: "",
      });
      onChange();
      toast.success("Maintenance window scheduled");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this maintenance window?")) return;
    try {
      await deleteMaintenanceWindow(id);
      onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const counts = {
    active: windows.filter(isActive).length,
    scheduled: windows.filter(isScheduled).length,
    past: windows.filter(isPast).length,
  };

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap gap-1 rounded-md border border-border-default bg-bg-surface p-1">
          {(["active", "scheduled", "past"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={`rounded px-3 py-1.5 text-sm capitalize transition ${
                view === v
                  ? "bg-bg-elevated text-fg-primary"
                  : "text-fg-secondary hover:text-fg-primary"
              }`}
            >
              {v} <span className="text-fg-tertiary">({counts[v]})</span>
            </button>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <div>
            <Label className="text-xs">From</Label>
            <Input
              type="datetime-local"
              value={rangeFrom}
              onChange={(e) => setRangeFrom(e.target.value)}
            />
          </div>
          <div>
            <Label className="text-xs">To</Label>
            <Input
              type="datetime-local"
              value={rangeTo}
              onChange={(e) => setRangeTo(e.target.value)}
            />
          </div>
          {(rangeFrom || rangeTo) && (
            <Button
              variant="ghost"
              onClick={() => {
                setRangeFrom("");
                setRangeTo("");
              }}
            >
              Clear
            </Button>
          )}
          <Button onClick={() => setOpen(true)}>
            <PlusCircle className="h-4 w-4" /> New window
          </Button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          title={`No ${view} maintenance windows`}
          description={
            view === "scheduled"
              ? "Schedule a window to suppress paging during planned downtime. Page-mode incidents during the window are suppressed; escalate_immediate is never downgraded."
              : view === "active"
                ? "No windows are currently in effect."
                : "No past windows match the current range filter."
          }
        />
      ) : (
        <ul className="divide-y divide-border-default rounded-lg border border-border-default bg-bg-surface">
          {filtered.map((w) => (
            <li key={w.id} className="flex items-start justify-between px-4 py-3">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Calendar className="h-4 w-4 text-fg-secondary" />
                  <span className="font-medium text-fg-primary">{w.name}</span>
                  <Badge variant="default">{scopeLabelFor(w)}</Badge>
                </div>
                <div className="text-xs text-fg-secondary">
                  {formatRange(w.starts_at, w.ends_at)}
                </div>
                {w.description && (
                  <div className="text-xs text-fg-tertiary">{w.description}</div>
                )}
              </div>
              <Button variant="ghost" onClick={() => remove(w.id)} title="Delete">
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Schedule maintenance window"
      >
        <div className="space-y-3">
          <div>
            <Label>Name</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Database migration"
            />
          </div>
          <div>
            <Label>Description (optional)</Label>
            <Textarea
              value={form.description}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
              rows={2}
              placeholder="Upgrading primary cluster to v14"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Starts at</Label>
              <Input
                type="datetime-local"
                value={form.starts_at}
                onChange={(e) =>
                  setForm({ ...form, starts_at: e.target.value })
                }
              />
            </div>
            <div>
              <Label>Ends at</Label>
              <Input
                type="datetime-local"
                value={form.ends_at}
                onChange={(e) =>
                  setForm({ ...form, ends_at: e.target.value })
                }
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Scope</Label>
              <Select
                value={form.scope_type}
                onChange={(e) =>
                  setForm({
                    ...form,
                    scope_type: e.target.value as MaintenanceWindowScopeType,
                    scope_id: "",
                  })
                }
              >
                <option value="global">Global (all paging)</option>
                <option value="service">Service</option>
                <option value="roster">Roster</option>
                <option value="team">Team</option>
              </Select>
            </div>
            {form.scope_type !== "global" && (
              <div>
                <Label>Target {form.scope_type}</Label>
                <Select
                  value={form.scope_id}
                  onChange={(e) =>
                    setForm({ ...form, scope_id: e.target.value })
                  }
                >
                  <option value="">— pick one —</option>
                  {scopeOptionsFor(form.scope_type).map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </Select>
              </div>
            )}
          </div>
          <p className="text-xs text-fg-tertiary">
            Page-mode incidents inside the window are suppressed.{" "}
            <span className="font-medium">escalate_immediate</span> incidents
            still page through.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>Schedule</Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}

const ALL_CHANNELS: {
  key: NotificationChannelKey;
  label: string;
  helper: string;
  fields: { name: string; label: string; placeholder: string }[];
}[] = [
  {
    key: "slack_dm",
    label: "Slack DM",
    helper: "OpsMender DMs you via the org's Slack bot.",
    fields: [
      {
        name: "user_id",
        label: "Slack user ID",
        placeholder: "U01ABC123",
      },
    ],
  },
  {
    key: "teams_dm",
    label: "Teams DM",
    helper: "Posts to an incoming-webhook channel until Sprint 37 lands real DMs.",
    fields: [
      {
        name: "webhook_url",
        label: "Incoming webhook URL",
        placeholder: "https://outlook.office.com/webhook/…",
      },
    ],
  },
  {
    key: "email",
    label: "Email",
    helper: "SMTP delivery via the org's configured email settings.",
    fields: [
      {
        name: "address",
        label: "Email address",
        placeholder: "you@example.com",
      },
    ],
  },
  {
    key: "sms",
    label: "SMS",
    helper: "Twilio-delivered SMS to the number below.",
    fields: [
      {
        name: "phone_number",
        label: "Phone number (E.164)",
        placeholder: "+15551234567",
      },
    ],
  },
];

const ALL_PRIORITIES: Priority[] = ["P0", "P1", "P2", "P3"];

function NotificationPreferencesPanel() {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pref, setPref] = useState<UserNotificationPrefResponse | null>(null);
  const [channels, setChannels] = useState<
    Record<string, Record<string, string>>
  >({});
  const [routing, setRouting] = useState<
    Record<string, NotificationChannelKey[]>
  >({});
  const [quietEnabled, setQuietEnabled] = useState(false);
  const [quiet, setQuiet] = useState<QuietHoursConfig>({
    weekday: { start: "22:00", end: "07:00" },
    min_priority_to_break: "P1",
    time_zone: "UTC",
  });

  useEffect(() => {
    (async () => {
      try {
        const data = await getMyNotificationPreferences();
        setPref(data);
        setChannels(data.channels ?? {});
        setRouting(data.routing ?? {});
        if (data.quiet_hours) {
          setQuietEnabled(true);
          setQuiet({
            weekday: data.quiet_hours.weekday ?? {
              start: "22:00",
              end: "07:00",
            },
            min_priority_to_break:
              data.quiet_hours.min_priority_to_break ?? "P1",
            time_zone: data.quiet_hours.time_zone ?? "UTC",
          });
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleChannel = (key: NotificationChannelKey, on: boolean) => {
    if (on) {
      const spec = ALL_CHANNELS.find((c) => c.key === key)!;
      const seed = Object.fromEntries(spec.fields.map((f) => [f.name, ""]));
      setChannels({ ...channels, [key]: seed });
    } else {
      const next = { ...channels };
      delete next[key];
      setChannels(next);
      const nextRouting = { ...routing };
      for (const p of ALL_PRIORITIES) {
        if (nextRouting[p]) {
          nextRouting[p] = nextRouting[p].filter((c) => c !== key);
        }
      }
      setRouting(nextRouting);
    }
  };

  const setChannelField = (
    key: NotificationChannelKey,
    field: string,
    value: string,
  ) => {
    setChannels({
      ...channels,
      [key]: { ...(channels[key] ?? {}), [field]: value },
    });
  };

  const toggleRoute = (
    priority: Priority,
    channel: NotificationChannelKey,
    on: boolean,
  ) => {
    const current = routing[priority] ?? [];
    const next = on
      ? Array.from(new Set([...current, channel]))
      : current.filter((c) => c !== channel);
    setRouting({ ...routing, [priority]: next });
  };

  const save = async () => {
    setSaving(true);
    try {
      const quiet_hours: QuietHoursConfig | null = quietEnabled
        ? {
            weekday: quiet.weekday ?? null,
            min_priority_to_break: quiet.min_priority_to_break ?? null,
            time_zone: quiet.time_zone ?? "UTC",
          }
        : null;
      const updated = await updateMyNotificationPreferences({
        channels,
        routing,
        quiet_hours,
      });
      setPref(updated);
      toast.success("Notification preferences saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <p className="text-sm text-fg-secondary">Loading your preferences…</p>
    );
  }

  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-fg-primary">Channels</h2>
        <p className="text-sm text-fg-secondary">
          Pick how OpsMender can reach you, then enter the destination for
          each enabled channel.
        </p>
        <ul className="mt-3 space-y-3">
          {ALL_CHANNELS.map((c) => {
            const enabled = channels[c.key] !== undefined;
            return (
              <li
                key={c.key}
                className="rounded-lg border border-border-default bg-bg-surface p-4"
              >
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(e) => toggleChannel(c.key, e.target.checked)}
                  />
                  <span className="font-medium text-fg-primary">
                    {c.label}
                  </span>
                  <span className="text-xs text-fg-tertiary">{c.helper}</span>
                </label>
                {enabled && (
                  <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {c.fields.map((f) => (
                      <div key={f.name}>
                        <Label className="text-xs">{f.label}</Label>
                        <Input
                          value={channels[c.key]?.[f.name] ?? ""}
                          placeholder={f.placeholder}
                          onChange={(e) =>
                            setChannelField(c.key, f.name, e.target.value)
                          }
                        />
                      </div>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      <div>
        <h2 className="text-lg font-semibold text-fg-primary">
          Routing by priority
        </h2>
        <p className="text-sm text-fg-secondary">
          Pick which enabled channels fire for each incident priority.
          Unchecked channels are skipped even if globally enabled.
        </p>
        <div className="mt-3 overflow-x-auto rounded-lg border border-border-default bg-bg-surface">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-border-default text-xs text-fg-secondary">
                <th className="px-3 py-2 text-left font-medium">Priority</th>
                {ALL_CHANNELS.map((c) => (
                  <th
                    key={c.key}
                    className="px-3 py-2 text-center font-medium"
                  >
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ALL_PRIORITIES.map((p) => (
                <tr key={p} className="border-b border-border-default last:border-0">
                  <td className="px-3 py-2 font-mono text-fg-primary">{p}</td>
                  {ALL_CHANNELS.map((c) => {
                    const enabled = channels[c.key] !== undefined;
                    const selected = (routing[p] ?? []).includes(c.key);
                    return (
                      <td
                        key={c.key}
                        className="px-3 py-2 text-center"
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          disabled={!enabled}
                          onChange={(e) =>
                            toggleRoute(p, c.key, e.target.checked)
                          }
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold text-fg-primary">Quiet hours</h2>
        <p className="text-sm text-fg-secondary">
          During quiet hours, incidents below the break threshold are
          suppressed for you. Higher priorities still page through.
        </p>
        <label className="mt-3 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={quietEnabled}
            onChange={(e) => setQuietEnabled(e.target.checked)}
          />
          Enable quiet hours
        </label>
        {quietEnabled && (
          <div className="mt-3 grid grid-cols-1 gap-3 rounded-lg border border-border-default bg-bg-surface p-4 sm:grid-cols-4">
            <div>
              <Label className="text-xs">Start</Label>
              <Input
                type="time"
                value={quiet.weekday?.start ?? ""}
                onChange={(e) =>
                  setQuiet({
                    ...quiet,
                    weekday: {
                      start: e.target.value,
                      end: quiet.weekday?.end ?? "07:00",
                    },
                  })
                }
              />
            </div>
            <div>
              <Label className="text-xs">End</Label>
              <Input
                type="time"
                value={quiet.weekday?.end ?? ""}
                onChange={(e) =>
                  setQuiet({
                    ...quiet,
                    weekday: {
                      start: quiet.weekday?.start ?? "22:00",
                      end: e.target.value,
                    },
                  })
                }
              />
            </div>
            <div>
              <Label className="text-xs">Break for priority ≥</Label>
              <Select
                value={quiet.min_priority_to_break ?? ""}
                onChange={(e) =>
                  setQuiet({
                    ...quiet,
                    min_priority_to_break:
                      (e.target.value || null) as Priority | null,
                  })
                }
              >
                <option value="">Never break</option>
                {ALL_PRIORITIES.map((p) => (
                  <option key={p} value={p}>
                    {p} and higher
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label className="text-xs">Time zone</Label>
              <Input
                value={quiet.time_zone ?? "UTC"}
                onChange={(e) =>
                  setQuiet({ ...quiet, time_zone: e.target.value })
                }
                placeholder="UTC"
              />
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between">
        <div className="text-xs text-fg-tertiary">
          {pref &&
            `Last updated ${new Date(pref.updated_at).toLocaleString()}`}
        </div>
        <Button onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save preferences"}
        </Button>
      </div>
    </section>
  );
}
