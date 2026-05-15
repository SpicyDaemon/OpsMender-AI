"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Calendar,
  ListOrdered,
  PlusCircle,
  Trash2,
  Users,
} from "lucide-react";

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
  listEscalationChains,
  listEscalationSteps,
  listPriorityRules,
  listRosters,
  listServices,
  listTeams,
} from "@/lib/api";
import type {
  EscalationChainResponse,
  EscalationStepResponse,
  EscalationTargetType,
  Priority,
  PriorityRuleResponse,
  ResponseMode,
  RosterResponse,
  ServiceResponse,
  TeamResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";

type Tab = "teams" | "services" | "rosters" | "rules" | "chains";

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
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [rosters, setRosters] = useState<RosterResponse[]>([]);
  const [rules, setRules] = useState<PriorityRuleResponse[]>([]);
  const [chains, setChains] = useState<EscalationChainResponse[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [t, s, r, p, c] = await Promise.all([
        listTeams(),
        listServices(),
        listRosters(),
        listPriorityRules(),
        listEscalationChains(),
      ]);
      setTeams(t.items);
      setServices(s.items);
      setRosters(r.items);
      setRules(p.items);
      setChains(c.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }, [toast]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="space-y-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold text-fg-primary">Paging</h1>
        <p className="text-sm text-fg-secondary">
          Teams, services, rosters, priority rules, and escalation chains —
          OpsMender-owned paging. Maintenance windows and channel fan-out land
          in Sprint 35.
        </p>
      </header>

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
