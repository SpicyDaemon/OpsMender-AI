"use client";

import {
  type ReactNode,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import {
  Bell,
  BellOff,
  Calendar,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  GitBranch,
  Info,
  ListOrdered,
  Pencil,
  PlusCircle,
  Repeat,
  Search,
  Send,
  Server,
  Trash2,
  Users,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";

import { RosterCalendarModal } from "@/components/RosterCalendarModal";
import { NotificationChannelsPage } from "@/components/NotificationChannelsPage";
import { useAuth } from "@/context/auth";

import {
  approveMaintenanceWindow,
  createMaintenanceWindow,
  deleteMaintenanceWindow,
  listMaintenanceWindows,
  rejectMaintenanceWindow,
  updateMaintenanceWindow,
} from "@/lib/api_reliability";
import {
  addEscalationStep,
  addRosterMember,
  addTeamMember,
  createEscalationChain,
  createRoster,
  createService,
  createTeam,
  deleteEscalationChain,
  deleteEscalationStep,
  deleteRoster,
  deleteService,
  deleteTeam,
  getConfig,
  getEscalationChainCalendar,
  getMyNotificationPreferences,
  linkServiceEscalationChain,
  listBotConnectors,
  listChainServices,
  listEscalationChains,
  listEscalationSteps,
  listIncidents,
  listMCPServers,
  listModelConfigs,
  listRosterMembers,
  listRosters,
  listServiceEscalationChains,
  listServices,
  listTeamMembers,
  listTeams,
  listUsers,
  removeRosterMember,
  removeTeamMember,
  reorderEscalationSteps,
  reorderRosterMembers,
  resolveOnCall,
  testMyNotificationPreferences,
  unlinkServiceEscalationChain,
  updateEscalationChain,
  updateEscalationStep,
  updateMyNotificationPreferences,
  updateRoster,
  updateService,
  updateTeam,
} from "@/lib/api";
import type {
  BotConnectorResponse,
  ChainWhereUsedItem,
  EscalationCalendarLevel,
  EscalationCalendarResponse,
  EscalationChainResponse,
  EscalationStepResponse,
  EscalationTargetType,
  IncidentResponse,
  MaintenanceWindowResponse,
  MaintenanceWindowScopeType,
  MCPServerResponse,
  ModelConfigResponse,
  NotificationChannelKey,
  Priority,
  QuietHoursConfig,
  RosterResponse,
  RoutingStage,
  ServiceResponse,
  TeamResponse,
  UserNotificationPrefResponse,
  UserResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { MultiSelect, type MultiSelectOption } from "@/components/ui/MultiSelect";
import { PageHeader } from "@/components/ui/PageHeader";
import { PagingFilterBar } from "@/components/ui/PagingFilterBar";
import { useToast } from "@/components/ui/Toast";
import {
  eligibleRosterMemberOptions,
  keepRosterMembersOnTeam,
} from "@/lib/rosterEligibility";
import { fullIntakeUrl } from "@/lib/intake";

export type Tab =
  | "teams"
  | "chains"
  | "services"
  | "rosters"
  | "maintenance"
  | "notifications";

export const TAB_SLUGS: Record<Tab, string> = {
  teams: "teams",
  chains: "escalation-chains",
  services: "services",
  rosters: "rosters",
  maintenance: "maintenance-windows",
  notifications: "notifications",
};

export const TABS: {
  id: Tab;
  label: string;
  description: string;
  icon: LucideIcon;
}[] = [
  {
    id: "teams",
    label: "Teams",
    description: "Org-chart units that group services and rosters.",
    icon: Users,
  },
  {
    id: "chains",
    label: "Escalation Chains",
    description: "Team-owned levels that page operators and backups.",
    icon: GitBranch,
  },
  {
    id: "services",
    label: "Services",
    description: "Alert sources with fixed priority, intake URL, and preferred MCP servers.",
    icon: Server,
  },
  {
    id: "rosters",
    label: "Rosters",
    description: "Schedules with coverage windows and ordered rotations.",
    icon: Repeat,
  },
  {
    id: "maintenance",
    label: "Maintenance Windows",
    description: "Suppress paging during planned downtime.",
    icon: Wrench,
  },
  {
    id: "notifications",
    label: "Notifications",
    description: "Operator delivery, viewer updates, quiet hours, routing, and chat sessions.",
    icon: Bell,
  },
];

function normalizeSlugInput(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-");
}

/** Inline copy-to-clipboard button used beside intake URLs. */
function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      size="sm"
      title={`Copy ${label.toLowerCase()}`}
      aria-label={`Copy ${label.toLowerCase()}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable — non-fatal */
        }
      }}
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </Button>
  );
}

const PRIORITY_VARIANT: Record<Priority, string> = {
  P0: "critical",
  P1: "high",
  P2: "medium",
  P3: "low",
};

const PRIORITY_OPTIONS: { value: Priority; label: string }[] = [
  { value: "P0", label: "P0 Critical" },
  { value: "P1", label: "P1 High" },
  { value: "P2", label: "P2 Medium" },
  { value: "P3", label: "P3 Low" },
];

export function PagingShell({ initialTab }: { initialTab: Tab }) {
  const toast = useToast();
  const router = useRouter();
  const tab = initialTab;
  const { user } = useAuth();
  // Operators can view paging setup in read-only mode; only admins can create/edit/delete.
  const canEdit = user?.role === "admin";
  const [showFlow, setShowFlow] = useState(false);
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [rosters, setRosters] = useState<RosterResponse[]>([]);
  const [chains, setChains] = useState<EscalationChainResponse[]>([]);
  const [windows, setWindows] = useState<MaintenanceWindowResponse[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [t, s, r, c, mw] = await Promise.all([
        listTeams(),
        listServices(),
        listRosters(),
        listEscalationChains(),
        listMaintenanceWindows(),
      ]);
      setTeams(t.items);
      setServices(s.items);
      setRosters(r.items);
      setChains(c.items);
      setWindows(mw.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }, [toast]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const activeTab = TABS.find((t) => t.id === tab) ?? TABS[0];

  return (
    <div className="space-y-6 p-6">
      <PageHeader
        title="Paging"
        subtitle="Teams, escalation chains, services, rosters, maintenance windows, and notifications — the OpsMender-owned paging surface."
        actions={
          <Button
            variant="ghost"
            onClick={() => setShowFlow(true)}
            title="How paging works"
          >
            <Info className="h-4 w-4" /> How it works
          </Button>
        }
      />

      <PagingFlowModal open={showFlow} onClose={() => setShowFlow(false)} />

      <nav
        aria-label="Paging sections"
        className="flex flex-wrap gap-2"
      >
        {TABS.map((t) => {
          const Icon = t.icon;
          const isActive = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => router.push(`/dashboard/paging/${TAB_SLUGS[t.id]}`)}
              aria-current={isActive ? "page" : undefined}
              className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition ${
                isActive
                  ? "bg-accent text-white shadow-sm"
                  : "border border-border-default bg-bg-surface text-fg-secondary hover:border-border-strong hover:text-fg-primary"
              }`}
            >
              <Icon className="h-4 w-4" />
              {t.label}
            </button>
          );
        })}
      </nav>

      <div className="border-b border-border-subtle pb-3">
        <h2 className="text-lg font-semibold text-fg-primary">
          {activeTab.label}
        </h2>
        <p className="text-sm text-fg-secondary">{activeTab.description}</p>
      </div>

      {tab === "teams" && <TeamsPanel teams={teams} onChange={refresh} canEdit={canEdit} />}
      {tab === "services" && (
        <ServicesPanel
          services={services}
          teams={teams}
          rosters={rosters}
          chains={chains}
          onChange={refresh}
          canEdit={canEdit}
        />
      )}
      {tab === "rosters" && (
        <RostersPanel rosters={rosters} teams={teams} onChange={refresh} canEdit={canEdit} />
      )}
      {tab === "chains" && (
        <ChainsPanel
          chains={chains}
          teams={teams}
          rosters={rosters}
          onChange={refresh}
          canEdit={canEdit}
        />
      )}
      {tab === "maintenance" && (
        <MaintenanceWindowsPanel
          windows={windows}
          services={services}
          rosters={rosters}
          teams={teams}
          onChange={refresh}
          canEdit={canEdit}
        />
      )}
      {tab === "notifications" && (
        <NotificationsPanel
          services={services}
          teams={teams}
          chains={chains}
        />
      )}
    </div>
  );
}

function TeamsPanel({
  teams,
  onChange,
  canEdit,
}: {
  teams: TeamResponse[];
  onChange: () => void;
  canEdit?: boolean;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<TeamResponse | null>(null);
  const emptyForm = {
    name: "",
    slug: "",
    description: "",
    member_ids: [] as string[],
  };
  const [form, setForm] = useState(emptyForm);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [initialMemberIds, setInitialMemberIds] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);

  useEffect(() => {
    let cancelled = false;
    listUsers()
      .then((res) => {
        if (!cancelled) setUsers(res.items);
      })
      .catch(() => {
        /* membership picker degrades to empty */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const userOptions = useMemo<MultiSelectOption[]>(
    () =>
      users
        .filter((u) => u.is_active && !u.deleted_at)
        .map((u) => ({
          value: u.id,
          label: u.username,
          sublabel: `${u.email} · ${u.role}`,
        })),
    [users],
  );

  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm);
    setInitialMemberIds([]);
    setOpen(true);
  };

  const openEdit = async (team: TeamResponse) => {
    setEditing(team);
    let memberIds: string[] = [];
    try {
      const res = await listTeamMembers(team.id);
      memberIds = res.items.map((m) => m.user_id);
    } catch {
      /* non-fatal */
    }
    setInitialMemberIds(memberIds);
    setForm({
      name: team.name,
      slug: team.slug,
      description: team.description ?? "",
      member_ids: memberIds,
    });
    setOpen(true);
  };

  const reconcileMembers = async (teamId: string) => {
    const desired = new Set(form.member_ids);
    const current = new Set(initialMemberIds);
    try {
      for (const id of form.member_ids) {
        if (!current.has(id)) await addTeamMember(teamId, { user_id: id });
      }
      for (const id of initialMemberIds) {
        if (!desired.has(id)) await removeTeamMember(teamId, id);
      }
    } catch (err) {
      toast.error(
        `Team saved, but membership update failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  };

  const submit = async () => {
    const slug = normalizeSlugInput(form.slug);
    if (!form.name || (!editing && !slug)) {
      toast.error("Name and slug are required");
      return;
    }
    try {
      let teamId: string;
      if (editing) {
        const updated = await updateTeam(editing.id, {
          name: form.name,
          description: form.description,
        });
        teamId = updated.id;
      } else {
        const created = await createTeam({
          name: form.name,
          slug,
          description: form.description || undefined,
        });
        teamId = created.id;
      }
      await reconcileMembers(teamId);
      setOpen(false);
      setEditing(null);
      setForm(emptyForm);
      onChange();
      toast.success(editing ? "Team updated" : "Team created");
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

  const filteredTeams = useMemo(() => {
    const q = deferredSearch.trim().toLowerCase();
    if (!q) return teams;
    return teams.filter((t) =>
      [t.name, t.slug, t.description ?? ""].join(" ").toLowerCase().includes(q),
    );
  }, [teams, deferredSearch]);

  const teamColumns = useMemo<DataTableColumn<TeamResponse>[]>(
    () => [
      {
        id: "name",
        label: "Team",
        accessor: (t) => t.name,
        cell: (t) => (
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-fg-secondary" />
            <span className="font-medium text-fg-primary">{t.name}</span>
          </div>
        ),
        sortable: true,
      },
      {
        id: "slug",
        label: "Slug",
        accessor: (t) => t.slug,
        cell: (t) => (
          <Badge variant="default" className="font-mono normal-case tracking-normal">
            {t.slug.toLowerCase()}
          </Badge>
        ),
        sortable: true,
      },
      {
        id: "description",
        label: "Description",
        accessor: (t) => t.description ?? "",
        cell: (t) =>
          t.description ? (
            <span className="line-clamp-2 text-sm text-fg-secondary">
              {t.description}
            </span>
          ) : (
            <span className="text-fg-muted">—</span>
          ),
      },
      {
        id: "created_at",
        label: "Created",
        accessor: (t) => t.created_at,
        cell: (t) => (
          <span className="whitespace-nowrap text-sm text-fg-secondary">
            {new Date(t.created_at).toLocaleDateString()}
          </span>
        ),
        sortable: true,
      },
    ],
    [],
  );

  return (
    <section className="space-y-3">
      {teams.length === 0 ? (
        <EmptyState
          title="No teams yet"
          description="Create your first team to start grouping services and rosters."
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/paging-guide.md"
          learnMoreLabel="Paging guide"
          action={canEdit ? (
            <Button onClick={openCreate}>
              <PlusCircle className="h-4 w-4" /> New team
            </Button>
          ) : undefined}
        />
      ) : (
        <>
          <PagingFilterBar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search teams..."
            searchAriaLabel="Search teams"
            hasFilters={Boolean(search)}
            onClear={() => setSearch("")}
            action={
              canEdit ? (
                <Button size="sm" onClick={openCreate}>
                  <PlusCircle className="h-4 w-4" /> New team
                </Button>
              ) : null
            }
          />
          <DataTable
            rows={filteredTeams}
            columns={teamColumns}
            rowKey={(t) => t.id}
            storageKey="opsmender:teams-table"
            hideToolbar
            rowActions={canEdit ? (t) => (
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void openEdit(t)}
                  title="Edit team"
                >
                  <Pencil className="h-4 w-4" /> Edit
                </Button>
                <Button variant="ghost" size="sm" onClick={() => remove(t.id)} title="Delete">
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ) : undefined}
          />
        </>
      )}

      <Modal
        open={open}
        onClose={() => {
          setOpen(false);
          setEditing(null);
        }}
        title={editing ? "Edit team" : "New team"}
      >
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
              onChange={(e) =>
                setForm({ ...form, slug: normalizeSlugInput(e.target.value) })
              }
              placeholder="payments-team"
              disabled={Boolean(editing)}
            />
            {editing && (
              <p className="mt-1 text-xs text-fg-muted">
                Slug is fixed after creation.
              </p>
            )}
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
          <div>
            <Label>People on this team</Label>
            <MultiSelect
              ariaLabel="Team members"
              options={userOptions}
              selected={form.member_ids}
              onChange={(next) => setForm({ ...form, member_ids: next })}
              emptyLabel="No users available."
            />
            <p className="mt-1 text-xs text-fg-muted">
              Select one or more people to assign to this team.
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>{editing ? "Save" : "Create"}</Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}

interface ServiceRow {
  service: ServiceResponse;
  team_name: string | null;
  on_call_user_id: string | null;
  on_call_username: string | null;
  open_incidents: number;
  last_incident_at: string | null;
}

type ServiceTimeFilter = "" | "24h" | "7d" | "30d" | "never";

const SERVICE_TIME_OPTIONS: { value: ServiceTimeFilter; label: string }[] = [
  { value: "", label: "All time" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "never", label: "No incidents" },
];

function serviceRowMatchesTime(row: ServiceRow, value: ServiceTimeFilter) {
  if (!value) return true;
  if (value === "never") return row.last_incident_at === null;
  if (!row.last_incident_at) return false;
  const last = new Date(row.last_incident_at).getTime();
  if (Number.isNaN(last)) return false;
  const now = Date.now();
  const windows: Record<Exclude<ServiceTimeFilter, "" | "never">, number> = {
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
  };
  return now - last <= windows[value];
}

function ServicesPanel({
  services,
  teams,
  rosters,
  chains,
  onChange,
  canEdit,
}: {
  services: ServiceResponse[];
  teams: TeamResponse[];
  rosters: RosterResponse[];
  chains: EscalationChainResponse[];
  onChange: () => void;
  canEdit?: boolean;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ServiceResponse | null>(null);
  const [publicBaseUrl, setPublicBaseUrl] = useState<string | null>(null);
  const emptyForm = {
    name: "",
    slug: "",
    team_id: "",
    description: "",
    priority: "P2" as Priority,
    preferred_mcp_server_ids: [] as string[],
    preferred_model_config_ids: [] as string[],
    ai_default_tier: "",
    escalation_chain_id: "",
    is_active: true,
  };
  const [form, setForm] = useState(emptyForm);
  const [serviceSearch, setServiceSearch] = useState("");
  const [teamFilter, setTeamFilter] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [coverageFilter, setCoverageFilter] = useState<string[]>([]);
  const [incidentFilter, setIncidentFilter] = useState<string[]>([]);
  const [timeFilter, setTimeFilter] = useState<ServiceTimeFilter>("");
  const [incidents, setIncidents] = useState<IncidentResponse[]>([]);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServerResponse[]>([]);
  const [modelConfigs, setModelConfigs] = useState<ModelConfigResponse[]>([]);
  const [onCallByTeam, setOnCallByTeam] = useState<Map<string, string | null>>(
    new Map(),
  );
  const deferredServiceSearch = useDeferredValue(serviceSearch);

  useEffect(() => {
    if (teams.length > 0 && !form.team_id) {
      setForm((f) => ({ ...f, team_id: teams[0].id }));
    }
  }, [teams, form.team_id]);

  // Resolve the public base URL once so intake URLs render absolute.
  useEffect(() => {
    let cancelled = false;
    getConfig()
      .then((cfg) => {
        if (!cancelled) setPublicBaseUrl(cfg.public_base_url ?? null);
      })
      .catch(() => {
        /* fall back to window.location.origin */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Enrich rows: load incidents + users + per-team on-call resolution.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [incList, uList, mcpList, modelList] = await Promise.all([
          listIncidents({ limit: 200 }).catch(() => ({
            items: [] as IncidentResponse[],
            total: 0,
          })),
          listUsers().catch(() => ({ items: [] as UserResponse[], total: 0 })),
          listMCPServers().catch(() => ({ items: [] as MCPServerResponse[], total: 0 })),
          listModelConfigs().catch(() => ({
            items: [] as ModelConfigResponse[],
            total: 0,
          })),
        ]);
        if (cancelled) return;
        setIncidents(incList.items);
        setUsers(uList.items);
        setMcpServers(mcpList.items);
        setModelConfigs(modelList.items);

        // Resolve on-call once per team via the team's first roster.
        const teamRoster = new Map<string, string>(); // team_id → roster_id
        for (const r of rosters) {
          if (!teamRoster.has(r.team_id)) teamRoster.set(r.team_id, r.id);
        }
        const entries = await Promise.all(
          Array.from(teamRoster.entries()).map(async ([teamId, rosterId]) => {
            try {
              const res = await resolveOnCall(rosterId);
              return [teamId, res.user_id] as const;
            } catch {
              return [teamId, null] as const;
            }
          }),
        );
        if (cancelled) return;
        setOnCallByTeam(new Map(entries));
      } catch (err) {
        if (!cancelled) {
          toast.error(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [rosters, toast]);

  // Reconcile the single primary escalation-chain link for a service:
  // unlink any chains other than the chosen one, link the chosen one.
  const reconcileChainLink = async (
    serviceId: string,
    desiredChainId: string,
  ) => {
    try {
      const current = await listServiceEscalationChains(serviceId);
      const linkedIds = current.items.map((l) => l.chain_id);
      for (const linkedId of linkedIds) {
        if (linkedId !== desiredChainId) {
          await unlinkServiceEscalationChain(serviceId, linkedId);
        }
      }
      if (desiredChainId && !linkedIds.includes(desiredChainId)) {
        await linkServiceEscalationChain(serviceId, desiredChainId);
      }
    } catch (err) {
      toast.error(
        `Service saved, but escalation chain link failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  };

  const submit = async () => {
    if (!form.name || !form.slug || !form.team_id) {
      toast.error("Name, slug, and team are required");
      return;
    }
    try {
      const payload = {
        team_id: form.team_id,
        name: form.name,
        description: form.description || undefined,
        priority: form.priority,
        preferred_mcp_server_ids: form.preferred_mcp_server_ids,
        preferred_model_config_ids: form.preferred_model_config_ids,
        ai_default_tier:
          form.ai_default_tier === "" ? null : Number(form.ai_default_tier),
        is_active: form.is_active,
      };
      let serviceId: string;
      if (editing) {
        const updated = await updateService(editing.id, payload);
        serviceId = updated.id;
      } else {
        const created = await createService({
          ...payload,
          slug: form.slug,
        });
        serviceId = created.id;
      }
      await reconcileChainLink(serviceId, form.escalation_chain_id);
      setOpen(false);
      setEditing(null);
      setForm({ ...emptyForm, team_id: teams[0]?.id ?? "" });
      onChange();
      toast.success(editing ? "Service updated" : "Service created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ ...emptyForm, team_id: teams[0]?.id ?? "" });
    setOpen(true);
  };

  const openEdit = async (service: ServiceResponse) => {
    setEditing(service);
    let chainId = "";
    try {
      const links = await listServiceEscalationChains(service.id);
      chainId = links.items[0]?.chain_id ?? "";
    } catch {
      /* non-fatal — leave unselected */
    }
    setForm({
      name: service.name,
      slug: service.slug,
      team_id: service.team_id,
      description: service.description ?? "",
      priority: service.priority,
      preferred_mcp_server_ids: service.preferred_mcp_server_ids ?? [],
      preferred_model_config_ids: service.preferred_model_config_ids ?? [],
      ai_default_tier:
        service.ai_default_tier == null ? "" : String(service.ai_default_tier),
      escalation_chain_id: chainId,
      is_active: service.is_active,
    });
    setOpen(true);
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

  // Aggregate per-service incident stats.
  const incidentStatsByService = useMemo(() => {
    const stats = new Map<
      string,
      { open: number; last_at: string | null }
    >();
    for (const inc of incidents) {
      if (!inc.service_id) continue;
      const cur = stats.get(inc.service_id) ?? { open: 0, last_at: null };
      if (inc.status === "open" || inc.status === "in_progress") {
        cur.open += 1;
      }
      if (!cur.last_at || inc.created_at > cur.last_at) {
        cur.last_at = inc.created_at;
      }
      stats.set(inc.service_id, cur);
    }
    return stats;
  }, [incidents]);

  const userById = useMemo(() => {
    const map = new Map<string, string>();
    for (const u of users) map.set(u.id, u.username);
    return map;
  }, [users]);

  const rows: ServiceRow[] = useMemo(() => {
    return services.map((s) => {
      const team = teams.find((t) => t.id === s.team_id);
      const onCallId = onCallByTeam.get(s.team_id) ?? null;
      const stats = incidentStatsByService.get(s.id);
      return {
        service: s,
        team_name: team?.name ?? null,
        on_call_user_id: onCallId,
        on_call_username: onCallId ? userById.get(onCallId) ?? null : null,
        open_incidents: stats?.open ?? 0,
        last_incident_at: stats?.last_at ?? null,
      };
    });
  }, [services, teams, onCallByTeam, incidentStatsByService, userById]);

  const filteredRows = useMemo(() => {
    const query = deferredServiceSearch.trim().toLowerCase();
    return rows.filter((row) => {
      if (query) {
        const haystack = [
          row.service.name,
          row.service.slug,
          row.service.description ?? "",
          row.team_name ?? "",
          row.on_call_username ?? "",
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      if (teamFilter.length && !teamFilter.includes(row.service.team_id))
        return false;
      if (statusFilter.length) {
        const status = row.service.is_active ? "active" : "inactive";
        if (!statusFilter.includes(status)) return false;
      }
      if (coverageFilter.length) {
        const coverage = row.on_call_username ? "covered" : "uncovered";
        if (!coverageFilter.includes(coverage)) return false;
      }
      if (incidentFilter.length) {
        const incidentState = row.open_incidents > 0 ? "has_open" : "no_open";
        if (!incidentFilter.includes(incidentState)) return false;
      }
      if (!serviceRowMatchesTime(row, timeFilter)) return false;
      return true;
    });
  }, [
    coverageFilter,
    deferredServiceSearch,
    incidentFilter,
    rows,
    statusFilter,
    teamFilter,
    timeFilter,
  ]);

  const hasServiceFilters = Boolean(
    serviceSearch ||
      teamFilter.length ||
      statusFilter.length ||
      coverageFilter.length ||
      incidentFilter.length ||
      timeFilter,
  );

  const columns: DataTableColumn<ServiceRow>[] = [
    {
      id: "name",
      label: "Service",
      accessor: (r) => r.service.name,
      cell: (r) => (
        <div>
          <div className="font-medium text-fg-primary">{r.service.name}</div>
          <div className="text-[11px] text-fg-muted">
            {r.service.slug.toLowerCase()}
          </div>
        </div>
      ),
      sortable: true,
    },
    {
      id: "priority",
      label: "Priority",
      accessor: (r) => r.service.priority,
      cell: (r) => (
        <Badge variant={PRIORITY_VARIANT[r.service.priority] as never}>
          {r.service.priority}
        </Badge>
      ),
      sortable: true,
    },
    {
      id: "intake",
      label: "Alert Intake",
      accessor: (r) => r.service.intake_url ?? "",
      cell: (r) => {
        const full = fullIntakeUrl(r.service.intake_url, publicBaseUrl);
        if (!full) {
          return <span className="text-[11px] text-fg-muted">not generated</span>;
        }
        return (
          <span className="inline-flex max-w-[22rem] items-center gap-1">
            <span className="truncate font-mono text-[11px] text-fg-secondary" title={full}>
              {full}
            </span>
            <CopyButton value={full} label="intake URL" />
          </span>
        );
      },
    },
    {
      id: "team",
      label: "Team",
      accessor: (r) => r.team_name ?? "",
      cell: (r) => r.team_name ?? "—",
      sortable: true,
    },
    {
      id: "on_call_now",
      label: "On call now",
      accessor: (r) => r.on_call_username ?? "",
      cell: (r) =>
        r.on_call_username ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-status-low" />
            <span>{r.on_call_username}</span>
          </span>
        ) : (
          <span className="text-fg-muted">—</span>
        ),
      sortable: true,
    },
    {
      id: "open_incidents",
      label: "Open",
      accessor: (r) => r.open_incidents,
      cell: (r) =>
        r.open_incidents > 0 ? (
          <Badge variant="open">{r.open_incidents}</Badge>
        ) : (
          <span className="text-fg-muted">0</span>
        ),
      sortable: true,
      align: "right",
    },
    {
      id: "last_incident",
      label: "Last incident",
      accessor: (r) => r.last_incident_at ?? "",
      cell: (r) =>
        r.last_incident_at ? (
          new Date(r.last_incident_at).toLocaleString()
        ) : (
          <span className="text-fg-muted">—</span>
        ),
      sortable: true,
    },
    {
      id: "status",
      label: "Status",
      accessor: (r) => (r.service.is_active ? "active" : "inactive"),
      cell: (r) =>
        r.service.is_active ? (
          <Badge variant="resolved">active</Badge>
        ) : (
          <Badge variant="closed">inactive</Badge>
        ),
      sortable: true,
    },
  ];

  return (
    <section className="space-y-3">
      <div className="rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-secondary">
        <span className="font-medium text-fg-primary">Alert Intake:</span>{" "}
        each service is the natural home for a unique service webhook URL. The
        legacy token backend still works today, but first-time setup should
        think in terms of routing alerts to a service.
      </div>
      {services.length === 0 ? (
        <EmptyState
          title="No services yet"
          description={
            teams.length === 0
              ? "Create a team first, then add services to it."
              : "Add a service so incidents and inbound alerts can be routed to its owning team."
          }
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/paging-guide.md"
          learnMoreLabel="Paging guide"
          action={
            canEdit && teams.length > 0 ? (
              <Button onClick={openCreate}>
                <PlusCircle className="h-4 w-4" /> New service
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <PagingFilterBar
            search={serviceSearch}
            onSearchChange={setServiceSearch}
            searchPlaceholder="Search services..."
            searchAriaLabel="Search services"
            filters={[
              {
                id: "team",
                label: "teams",
                values: teamFilter,
                onChange: setTeamFilter,
                options: teams.map((t) => ({ value: t.id, label: t.name })),
              },
              {
                id: "status",
                label: "statuses",
                values: statusFilter,
                onChange: setStatusFilter,
                options: [
                  { value: "active", label: "Active" },
                  { value: "inactive", label: "Inactive" },
                ],
              },
              {
                id: "coverage",
                label: "coverage",
                values: coverageFilter,
                onChange: setCoverageFilter,
                options: [
                  { value: "covered", label: "On-call covered" },
                  { value: "uncovered", label: "No on-call" },
                ],
              },
              {
                id: "incident",
                label: "incident states",
                values: incidentFilter,
                onChange: setIncidentFilter,
                options: [
                  { value: "has_open", label: "Has open incidents" },
                  { value: "no_open", label: "No open incidents" },
                ],
              },
              {
                kind: "single",
                id: "time",
                label: "Filter services by last incident",
                value: timeFilter,
                onChange: (v) => setTimeFilter(v as ServiceTimeFilter),
                options: SERVICE_TIME_OPTIONS,
              },
            ]}
            hasFilters={hasServiceFilters}
            onClear={() => {
              setServiceSearch("");
              setTeamFilter([]);
              setStatusFilter([]);
              setCoverageFilter([]);
              setIncidentFilter([]);
              setTimeFilter("");
            }}
            action={
              canEdit ? (
                <Button
                  size="sm"
                  onClick={openCreate}
                  disabled={teams.length === 0}
                >
                  <PlusCircle className="h-4 w-4" /> New service
                </Button>
              ) : undefined
            }
          />
          <DataTable
            rows={filteredRows}
            columns={columns}
            rowKey={(r) => r.service.id}
            storageKey="opsmender:services-table"
            hideToolbar
            rowActions={canEdit ? (r) => (
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => openEdit(r.service)}
                  title="Edit service"
                >
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => remove(r.service.id)}
                  title="Delete service"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ) : undefined}
          />
        </>
      )}

      <Modal
        open={open}
        onClose={() => {
          setOpen(false);
          setEditing(null);
        }}
        title={editing ? "Edit service" : "New service"}
      >
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
              onChange={(e) =>
                setForm({ ...form, slug: normalizeSlugInput(e.target.value) })
              }
              placeholder="payments-api"
              disabled={Boolean(editing)}
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
              {PRIORITY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
            <p className="mt-1 text-xs text-fg-muted">
              Incidents created through this service use this priority. AI does
              not override it in v1.
            </p>
          </div>
          <div>
            <Label>Escalation chain (optional)</Label>
            <Select
              value={form.escalation_chain_id}
              onChange={(e) =>
                setForm({ ...form, escalation_chain_id: e.target.value })
              }
            >
              <option value="">— none —</option>
              {chains
                .filter((c) => !form.team_id || c.team_id === form.team_id)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
            </Select>
            <p className="mt-1 text-xs text-fg-muted">
              P0/P1 incidents page through this chain. Chains belong to the
              service&apos;s team.
            </p>
          </div>
          <div>
            <Label>Preferred MCP servers</Label>
            <MultiSelect
              ariaLabel="Preferred MCP servers"
              ordered
              options={mcpServers.map((s) => ({ value: s.id, label: s.name }))}
              selected={form.preferred_mcp_server_ids}
              onChange={(next) =>
                setForm({ ...form, preferred_mcp_server_ids: next })
              }
              emptyLabel="No MCP servers configured yet."
            />
            <p className="mt-1 text-xs text-fg-muted">
              Ordered preference list. OpsMender tries these first to reduce
              tool noise; operators can still ask for another configured MCP
              server manually.
            </p>
          </div>
          <div>
            <Label>Preferred Models</Label>
            <MultiSelect
              ariaLabel="Preferred Models"
              ordered
              maxSelections={3}
              options={modelConfigs.map((model) => ({
                value: model.id,
                label: model.name,
                sublabel: model.is_active
                  ? `${model.provider} / ${model.model_id}`
                  : "Unavailable / disabled",
                disabled: !model.is_active,
              }))}
              selected={form.preferred_model_config_ids}
              onChange={(next) =>
                setForm({ ...form, preferred_model_config_ids: next })
              }
              emptyLabel="No enabled models configured yet."
            />
            <p className="mt-1 text-xs text-fg-muted">
              OpsMender tries these models in order for incidents on this
              service. If none are available, it falls back to any enabled
              model.
            </p>
            <p className="mt-1 text-xs text-fg-muted">
              The model that ingests an incident becomes the default model for
              that incident&apos;s AI session when possible. Operators can
              still switch models during the session.
            </p>
          </div>
          <div>
            <Label>Default AI Autonomy Tier</Label>
            <Select
              value={form.ai_default_tier}
              onChange={(e) =>
                setForm({ ...form, ai_default_tier: e.target.value })
              }
            >
              <option value="">Inherit from MCP Skill / organization</option>
              <option value="0">Tier 0 — Autonomous</option>
              <option value="1">Tier 1 — Approval Required</option>
              <option value="2">Tier 2 — Advisory</option>
            </Select>
            <p className="mt-1 text-xs text-fg-muted">
              Tier 0 may auto-start after acknowledgment when organization
              policy allows it. Tier 1 and Tier 2 never auto-start.
            </p>
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
          <label className="flex items-center gap-2 text-sm text-fg-secondary">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
            />
            Active (inactive services keep their config but stop routing)
          </label>
          {editing &&
            (() => {
              const full = fullIntakeUrl(editing.intake_url, publicBaseUrl);
              if (!full) return null;
              return (
                <div>
                  <Label>Alert intake URL</Label>
                  <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-fg-secondary" title={full}>
                      {full}
                    </span>
                    <CopyButton value={full} label="intake URL" />
                  </div>
                  <p className="mt-1 text-xs text-fg-muted">
                    Point CloudWatch / Azure / GCP / OCI and other alerting
                    systems here. POST alerts to associate them with this
                    service.
                  </p>
                </div>
              );
            })()}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>{editing ? "Save" : "Create"}</Button>
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
  canEdit,
}: {
  rosters: RosterResponse[];
  teams: TeamResponse[];
  onChange: () => void;
  canEdit?: boolean;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<RosterResponse | null>(null);
  const [calendarFor, setCalendarFor] = useState<RosterResponse | null>(null);
  const [users, setUsers] = useState<UserResponse[]>([]);
  // user_ids on the currently-selected team. null = not loaded yet (so the
  // picker stays empty until we know the team's membership).
  const [teamMemberIds, setTeamMemberIds] = useState<Set<string> | null>(null);
  const [initialMemberIds, setInitialMemberIds] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [teamFilter, setTeamFilter] = useState<string[]>([]);
  const [patternFilter, setPatternFilter] = useState<string[]>([]);
  const [enabledFilter, setEnabledFilter] = useState<string[]>([]);
  const deferredSearch = useDeferredValue(search);
  const emptyForm = {
    name: "",
    team_id: "",
    description: "",
    time_zone: "UTC",
    pattern: "weekly" as "weekly" | "daily" | "custom_n_days",
    pattern_length: 7,
    coverage_start_time: "08:00",
    coverage_end_time: "18:00",
    anchor_date: new Date().toISOString().slice(0, 10),
    is_active: true,
    member_ids: [] as string[],
  };
  const [form, setForm] = useState(emptyForm);

  useEffect(() => {
    if (teams.length > 0 && !form.team_id) {
      setForm((f) => ({ ...f, team_id: teams[0].id }));
    }
  }, [teams, form.team_id]);

  useEffect(() => {
    let cancelled = false;
    listUsers()
      .then((res) => {
        if (!cancelled) setUsers(res.items);
      })
      .catch(() => {
        /* member picker degrades to empty */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Scope the member picker to the selected team. When the team changes, drop
  // any already-selected members who aren't on the new team (the backend
  // enforces this too) and explain why.
  useEffect(() => {
    if (!open) return;
    const teamId = form.team_id;
    if (!teamId) {
      setTeamMemberIds(new Set());
      return;
    }
    let cancelled = false;
    setTeamMemberIds(null);
    listTeamMembers(teamId)
      .then((res) => {
        if (cancelled) return;
        const ids = new Set(res.items.map((m) => m.user_id));
        setTeamMemberIds(ids);
        setForm((f) => {
          if (f.team_id !== teamId) return f;
          const kept = keepRosterMembersOnTeam(f.member_ids, ids);
          if (kept.length !== f.member_ids.length) {
            toast.warning(
              "Some selected members were removed because they are not part of the selected team.",
            );
            return { ...f, member_ids: kept };
          }
          return f;
        });
      })
      .catch(() => {
        if (!cancelled) setTeamMemberIds(new Set());
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, form.team_id]);

  // Rotation members must be Admin/Operator users (Viewers can't operate) AND
  // belong to the selected team.
  const memberOptions = useMemo<MultiSelectOption[]>(
    () => eligibleRosterMemberOptions(users, teamMemberIds),
    [users, teamMemberIds],
  );

  const openCreate = () => {
    setEditing(null);
    setInitialMemberIds([]);
    setForm({ ...emptyForm, team_id: teams[0]?.id ?? "" });
    setOpen(true);
  };

  const openEdit = async (roster: RosterResponse) => {
    setEditing(roster);
    let memberIds: string[] = [];
    try {
      const res = await listRosterMembers(roster.id);
      memberIds = res.items
        .slice()
        .sort((a, b) => a.position_index - b.position_index)
        .map((m) => m.user_id);
    } catch {
      /* non-fatal */
    }
    setInitialMemberIds(memberIds);
    setForm({
      name: roster.name,
      team_id: roster.team_id,
      description: roster.description ?? "",
      time_zone: roster.time_zone,
      pattern: roster.pattern as "weekly" | "daily" | "custom_n_days",
      pattern_length: roster.pattern_length,
      coverage_start_time: roster.coverage_start_time,
      coverage_end_time: roster.coverage_end_time,
      anchor_date: roster.anchor_date,
      is_active: roster.is_active,
      member_ids: memberIds,
    });
    setOpen(true);
  };

  // Reconcile ordered rotation members: add new ones, remove dropped ones,
  // then push the desired order so position_index matches the chips.
  const reconcileMembers = async (rosterId: string) => {
    const desired = form.member_ids;
    const current = new Set(initialMemberIds);
    try {
      for (let i = 0; i < desired.length; i++) {
        if (!current.has(desired[i])) {
          await addRosterMember(rosterId, {
            user_id: desired[i],
            position_index: i,
          });
        }
      }
      for (const id of initialMemberIds) {
        if (!desired.includes(id)) await removeRosterMember(rosterId, id);
      }
      if (desired.length > 0) {
        await reorderRosterMembers(rosterId, desired);
      }
    } catch (err) {
      toast.error(
        `Roster saved, but rotation update failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  };

  const submit = async () => {
    if (!form.name || !form.team_id) {
      toast.error("Name and team required");
      return;
    }
    // Block save if any selected member isn't an eligible member of the team
    // (the backend rejects these too — this just fails fast before any write).
    if (teamMemberIds !== null) {
      const eligibleIds = new Set(memberOptions.map((o) => o.value));
      if (form.member_ids.some((id) => !eligibleIds.has(id))) {
        toast.error(
          "Some selected members are not eligible for the selected team.",
        );
        return;
      }
    }
    try {
      const payload = {
        team_id: form.team_id,
        name: form.name,
        description: form.description || undefined,
        time_zone: form.time_zone,
        pattern: form.pattern,
        pattern_length: form.pattern_length,
        coverage_start_time: form.coverage_start_time,
        coverage_end_time: form.coverage_end_time,
        handoff_time: form.coverage_start_time,
        anchor_date: form.anchor_date,
        is_active: form.is_active,
      };
      let rosterId: string;
      if (editing) {
        const updated = await updateRoster(editing.id, payload);
        rosterId = updated.id;
      } else {
        const created = await createRoster(payload);
        rosterId = created.id;
      }
      await reconcileMembers(rosterId);
      setOpen(false);
      setEditing(null);
      setForm({ ...emptyForm, team_id: teams[0]?.id ?? "" });
      onChange();
      toast.success(editing ? "Roster updated" : "Roster created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const toggleRoster = async (roster: RosterResponse) => {
    try {
      await updateRoster(roster.id, { is_active: !roster.is_active });
      onChange();
      toast.success(roster.is_active ? "Roster disabled" : "Roster enabled");
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

  const teamNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of teams) map.set(t.id, t.name);
    return map;
  }, [teams]);

  const teamFilterOptions = useMemo(
    () => teams.map((t) => ({ value: t.id, label: t.name })),
    [teams],
  );

  const filteredRosters = useMemo(() => {
    const q = deferredSearch.trim().toLowerCase();
    return rosters.filter((r) => {
      if (q) {
        const haystack = [r.name, teamNameById.get(r.team_id) ?? ""]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      if (teamFilter.length && !teamFilter.includes(r.team_id)) return false;
      if (patternFilter.length && !patternFilter.includes(r.pattern))
        return false;
      if (enabledFilter.length) {
        const state = r.is_active ? "enabled" : "disabled";
        if (!enabledFilter.includes(state)) return false;
      }
      return true;
    });
  }, [rosters, deferredSearch, teamFilter, patternFilter, enabledFilter, teamNameById]);

  const rosterColumns = useMemo<DataTableColumn<RosterResponse>[]>(
    () => [
      {
        id: "name",
        label: "Roster",
        accessor: (r) => r.name,
        sortable: true,
        cell: (r) => (
          <div className="flex items-center gap-2">
            <Calendar className="h-4 w-4 text-fg-secondary" />
            <span className="font-medium text-fg-primary">{r.name}</span>
          </div>
        ),
      },
      {
        id: "team",
        label: "Team",
        accessor: (r) => teamNameById.get(r.team_id) ?? "",
        sortable: true,
        cell: (r) => (
          <span className="text-fg-secondary">
            {teamNameById.get(r.team_id) ?? "—"}
          </span>
        ),
      },
      {
        id: "enabled",
        label: "Enabled",
        accessor: (r) => (r.is_active ? "enabled" : "disabled"),
        sortable: true,
        cell: (r) => (
          <Button variant="ghost" size="sm" onClick={() => toggleRoster(r)}>
            <Badge variant={r.is_active ? "resolved" : "closed"}>
              {r.is_active ? "Enabled" : "Disabled"}
            </Badge>
          </Button>
        ),
      },
      {
        id: "pattern",
        label: "Pattern",
        accessor: (r) => r.pattern,
        sortable: true,
        cell: (r) => (
          <span className="inline-flex items-center gap-2">
            <Badge variant="default">{r.pattern}</Badge>
            {r.pattern === "custom_n_days" && (
              <span className="text-xs text-fg-muted">
                {r.pattern_length}d
              </span>
            )}
          </span>
        ),
      },
      {
        id: "time_zone",
        label: "Time zone",
        accessor: (r) => r.time_zone,
        sortable: true,
        cell: (r) => (
          <span className="font-mono text-xs text-fg-secondary">
            {r.time_zone}
          </span>
        ),
      },
      {
        id: "coverage",
        label: "Coverage window",
        accessor: (r) => `${r.coverage_start_time}-${r.coverage_end_time}`,
        sortable: true,
        cell: (r) => (
          <span className="font-mono text-xs text-fg-secondary">
            {r.coverage_start_time} → {r.coverage_end_time}
          </span>
        ),
      },
    ],
    [teamNameById],
  );

  const hasFilters = Boolean(
    search || teamFilter.length || patternFilter.length || enabledFilter.length,
  );

  return (
    <section className="space-y-3">
      {rosters.length === 0 ? (
        <EmptyState
          title="No rosters yet"
          description="Rosters define who is on call. Add one per team to start."
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/paging-guide.md"
          learnMoreLabel="Paging guide"
          action={
            canEdit ? (
              <Button onClick={openCreate} disabled={teams.length === 0}>
                <PlusCircle className="h-4 w-4" /> New roster
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <PagingFilterBar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search rosters..."
            searchAriaLabel="Search rosters"
            filters={[
              {
                id: "team",
                label: "teams",
                values: teamFilter,
                onChange: setTeamFilter,
                options: teamFilterOptions,
              },
              {
                id: "pattern",
                label: "patterns",
                values: patternFilter,
                onChange: setPatternFilter,
                options: [
                  { value: "weekly", label: "Weekly" },
                  { value: "daily", label: "Daily" },
                  { value: "custom_n_days", label: "Custom" },
                ],
              },
              {
                id: "enabled",
                label: "states",
                values: enabledFilter,
                onChange: setEnabledFilter,
                options: [
                  { value: "enabled", label: "Enabled" },
                  { value: "disabled", label: "Disabled" },
                ],
              },
            ]}
            hasFilters={hasFilters}
            onClear={() => {
              setSearch("");
              setTeamFilter([]);
              setPatternFilter([]);
              setEnabledFilter([]);
            }}
            action={
              canEdit ? (
                <Button size="sm" onClick={openCreate} disabled={teams.length === 0}>
                  <PlusCircle className="h-4 w-4" /> New roster
                </Button>
              ) : null
            }
          />
          <DataTable
            rows={filteredRosters}
            columns={rosterColumns}
            rowKey={(r) => r.id}
            storageKey="opsmender:rosters-table"
            hideToolbar
            empty={
              <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
                No rosters match the current filters.
              </div>
            }
            rowActions={(r) => (
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setCalendarFor(r)}
                  title="Open calendar view"
                >
                  <CalendarDays className="h-4 w-4" /> Calendar
                </Button>
                {canEdit && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void openEdit(r)}
                    title="Edit roster"
                  >
                    <Pencil className="h-4 w-4" /> Edit
                  </Button>
                )}
                {canEdit && (
                  <Button variant="ghost" size="sm" onClick={() => remove(r.id)} title="Delete">
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            )}
          />
        </>
      )}

      <Modal
        open={open}
        onClose={() => {
          setOpen(false);
          setEditing(null);
        }}
        title={editing ? "Edit roster schedule" : "Create roster schedule"}
      >
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
          <div>
            <Label>Description (optional)</Label>
            <Textarea
              value={form.description}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
              rows={2}
              placeholder="Morning coverage for the DevOps team"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-fg-secondary">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) =>
                setForm({ ...form, is_active: e.target.checked })
              }
            />
            Enabled
          </label>
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
              <Label>Rotation length (days)</Label>
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
              <Label>Coverage start time</Label>
              <Input
                value={form.coverage_start_time}
                onChange={(e) =>
                  setForm({ ...form, coverage_start_time: e.target.value })
                }
                placeholder="08:00"
              />
            </div>
            <div>
              <Label>Coverage end time</Label>
              <Input
                value={form.coverage_end_time}
                onChange={(e) =>
                  setForm({ ...form, coverage_end_time: e.target.value })
                }
                placeholder="18:00"
              />
              <p className="mt-1 text-xs text-fg-muted">
                Overnight windows are supported, for example 18:00 → 08:00.
              </p>
            </div>
            <div className="col-span-2">
              <Label>Start Date</Label>
              <Input
                type="date"
                value={form.anchor_date}
                onChange={(e) =>
                  setForm({ ...form, anchor_date: e.target.value })
                }
              />
            </div>
          </div>
          <div>
            <Label>Rotation members (ordered)</Label>
            {!form.team_id ? (
              <p className="text-sm text-fg-muted">
                Select a team first to choose rotation members.
              </p>
            ) : memberOptions.length === 0 ? (
              <p className="text-sm text-fg-muted">
                No eligible team members. Add Admin or Operator users to this team
                before creating a roster.
              </p>
            ) : (
              <MultiSelect
                ariaLabel="Rotation members"
                ordered
                options={memberOptions}
                selected={form.member_ids}
                onChange={(next) => setForm({ ...form, member_ids: next })}
                emptyLabel="No eligible team members."
              />
            )}
            <p className="mt-1 text-xs text-fg-muted">
              Only Admin and Operator users assigned to the selected team can be
              added to this roster. Member 1 covers the first window, member 2 the
              next, and so on — then the cycle repeats.
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>{editing ? "Save" : "Create"}</Button>
          </div>
        </div>
      </Modal>

      {calendarFor && (
        <RosterCalendarModal
          roster={calendarFor}
          onClose={() => setCalendarFor(null)}
          onChange={onChange}
        />
      )}
    </section>
  );
}

function ChainsPanel({
  chains,
  teams,
  rosters,
  onChange,
  canEdit,
}: {
  chains: EscalationChainResponse[];
  teams: TeamResponse[];
  rosters: RosterResponse[];
  onChange: () => void;
  canEdit?: boolean;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<EscalationChainResponse | null>(null);
  const [form, setForm] = useState({ name: "", team_id: "", description: "" });
  const [expanded, setExpanded] = useState<string | null>(null);
  const [stepsByChain, setStepsByChain] = useState<
    Record<string, EscalationStepResponse[]>
  >({});
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [calendarFor, setCalendarFor] = useState<EscalationChainResponse | null>(null);
  const [search, setSearch] = useState("");
  const [teamFilter, setTeamFilter] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const deferredSearch = useDeferredValue(search);

  useEffect(() => {
    if (teams.length > 0 && !form.team_id) {
      setForm((f) => ({ ...f, team_id: teams[0].id }));
    }
  }, [teams, form.team_id]);

  useEffect(() => {
    let cancelled = false;
    listUsers()
      .then((res) => {
        if (!cancelled) setUsers(res.items);
      })
      .catch(() => {
        /* user-target picker degrades to empty */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: "", team_id: teams[0]?.id ?? "", description: "" });
    setOpen(true);
  };

  const openEdit = (chain: EscalationChainResponse) => {
    setEditing(chain);
    setForm({
      name: chain.name,
      team_id: chain.team_id,
      description: chain.description ?? "",
    });
    setOpen(true);
  };

  const submit = async () => {
    if (!form.name || !form.team_id) {
      toast.error("Name and team are required");
      return;
    }
    try {
      if (editing) {
        await updateEscalationChain(editing.id, {
          name: form.name,
          team_id: form.team_id,
          description: form.description || undefined,
        });
      } else {
        await createEscalationChain({
          team_id: form.team_id,
          name: form.name,
          description: form.description || undefined,
        });
      }
      setOpen(false);
      setEditing(null);
      setForm({ name: "", team_id: teams[0]?.id ?? "", description: "" });
      onChange();
      toast.success(editing ? "Chain updated" : "Chain created");
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

  const loadStepsFor = useCallback(
    async (chainId: string) => {
      try {
        const resp = await listEscalationSteps(chainId);
        setStepsByChain((prev) => ({ ...prev, [chainId]: resp.items }));
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    },
    [toast],
  );

  const toggleExpand = useCallback(
    async (chainId: string) => {
      if (expanded === chainId) {
        setExpanded(null);
        return;
      }
      await loadStepsFor(chainId);
      setExpanded(chainId);
    },
    [expanded, loadStepsFor],
  );

  const expandedKeys = useMemo(
    () => new Set<string>(expanded ? [expanded] : []),
    [expanded],
  );

  const teamNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of teams) map.set(t.id, t.name);
    return map;
  }, [teams]);

  const teamFilterOptions = useMemo(
    () => [
      ...teams.map((t) => ({ value: t.id, label: t.name })),
    ],
    [teams],
  );

  const filteredChains = useMemo(() => {
    const q = deferredSearch.trim().toLowerCase();
    return chains.filter((c) => {
      if (q) {
        const haystack = [c.name, teamNameById.get(c.team_id) ?? "", c.description ?? ""]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      if (teamFilter.length && !teamFilter.includes(c.team_id)) return false;
      if (statusFilter.length) {
        const status = c.is_active ? "active" : "inactive";
        if (!statusFilter.includes(status)) return false;
      }
      return true;
    });
  }, [chains, deferredSearch, teamFilter, statusFilter, teamNameById]);

  const chainColumns = useMemo<DataTableColumn<EscalationChainResponse>[]>(
    () => [
      {
        id: "name",
        label: "Chain",
        accessor: (c) => c.name,
        sortable: true,
        cell: (c) => (
          <div className="flex items-center gap-2">
            <ListOrdered className="h-4 w-4 text-fg-secondary" />
            <span className="font-medium text-fg-primary">{c.name}</span>
          </div>
        ),
      },
      {
        id: "team",
        label: "Team",
        accessor: (c) => teamNameById.get(c.team_id) ?? "",
        sortable: true,
        cell: (c) => (
          <span className="text-fg-secondary">
            {teamNameById.get(c.team_id) ?? "—"}
          </span>
        ),
      },
      {
        id: "description",
        label: "Description",
        accessor: (c) => c.description ?? "",
        cell: (c) =>
          c.description ? (
            <span className="line-clamp-2 max-w-[28rem] text-xs text-fg-secondary">
              {c.description}
            </span>
          ) : (
            <span className="text-fg-muted">—</span>
          ),
      },
      {
        id: "status",
        label: "Status",
        accessor: (c) => (c.is_active ? "active" : "inactive"),
        sortable: true,
        cell: (c) =>
          c.is_active ? (
            <Badge variant="resolved">active</Badge>
          ) : (
            <Badge variant="closed">inactive</Badge>
          ),
      },
    ],
    [teamNameById],
  );

  const hasFilters = Boolean(search || teamFilter.length || statusFilter.length);

  return (
    <section className="space-y-3">
      {chains.length === 0 ? (
        <EmptyState
          title="No escalation chains"
          description="Without a chain, incidents flagged for paging do not page anyone. Create one per team."
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/paging-guide.md"
          learnMoreLabel="Paging guide"
          action={
            canEdit ? (
              <Button onClick={openCreate} disabled={teams.length === 0}>
                <PlusCircle className="h-4 w-4" /> New chain
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <PagingFilterBar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search chains..."
            searchAriaLabel="Search escalation chains"
            filters={[
              {
                id: "team",
                label: "teams",
                values: teamFilter,
                onChange: setTeamFilter,
                options: teamFilterOptions,
              },
              {
                id: "status",
                label: "statuses",
                values: statusFilter,
                onChange: setStatusFilter,
                options: [
                  { value: "active", label: "Active" },
                  { value: "inactive", label: "Inactive" },
                ],
              },
            ]}
            hasFilters={hasFilters}
            onClear={() => {
              setSearch("");
              setTeamFilter([]);
              setStatusFilter([]);
            }}
            action={
              canEdit ? (
                <Button size="sm" onClick={openCreate} disabled={teams.length === 0}>
                  <PlusCircle className="h-4 w-4" /> New chain
                </Button>
              ) : null
            }
          />
          <DataTable
            rows={filteredChains}
            columns={chainColumns}
            rowKey={(c) => c.id}
            storageKey="opsmender:chains-table"
            hideToolbar
            empty={
              <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
                No chains match the current filters.
              </div>
            }
            expandedRow={{
              expandedKeys,
              onToggle: (key) => void toggleExpand(key),
              label: "Levels",
              render: (c) => (
                <StepsEditor
                  chainId={c.id}
                  steps={stepsByChain[c.id] ?? []}
                  rosters={rosters}
                  teams={teams}
                  users={users}
                  onChange={() => loadStepsFor(c.id)}
                />
              ),
            }}
            rowActions={(c) => (
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setCalendarFor(c)}
                  title="View escalation calendar"
                >
                  <CalendarDays className="h-4 w-4" /> Calendar
                </Button>
                {canEdit && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => openEdit(c)}
                    title="Edit chain"
                  >
                    <Pencil className="h-4 w-4" /> Edit
                  </Button>
                )}
                {canEdit && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => remove(c.id)}
                    title="Delete chain"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            )}
          />
        </>
      )}

      <Modal
        open={open}
        onClose={() => {
          setOpen(false);
          setEditing(null);
        }}
        title={editing ? "Edit escalation chain" : "New escalation chain"}
      >
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
          <p className="text-xs text-fg-muted">
            Levels are added after saving from the chain&apos;s expanded row. A
            chain with no levels pages no one — add at least Level 1.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>{editing ? "Save" : "Create"}</Button>
          </div>
        </div>
      </Modal>

      {calendarFor && (
        <EscalationCalendarModal
          chain={calendarFor}
          onClose={() => setCalendarFor(null)}
        />
      )}
    </section>
  );
}

type CalendarRange = "today" | "7d" | "30d" | "90d";

const CALENDAR_RANGES: { value: CalendarRange; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7D" },
  { value: "30d", label: "30D" },
  { value: "90d", label: "90D" },
];

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function fmtCalendarDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    weekday: "short",
  });
}

function calendarStatusLabel(status: EscalationCalendarLevel["status"]): string {
  switch (status) {
    case "covered":
      return "covered";
    case "disabled_roster":
      return "disabled roster";
    case "empty_roster":
      return "empty roster";
    case "inactive_user":
      return "inactive user";
    case "deleted_user":
      return "deleted user";
    case "outside_coverage":
      return "outside coverage";
    case "unassigned":
      return "unassigned";
    default:
      return "unknown";
  }
}

function calendarStatusVariant(status: EscalationCalendarLevel["status"]) {
  if (status === "covered") return "resolved";
  if (status === "disabled_roster" || status === "empty_roster") return "closed";
  if (status === "unknown" || status === "deleted_user") return "failed";
  return "pending";
}

function targetTypeLabel(targetType: EscalationCalendarLevel["target_type"]): string {
  if (targetType === "roster") return "Roster";
  if (targetType === "user") return "User";
  return "Team";
}

function EscalationCalendarModal({
  chain,
  onClose,
}: {
  chain: EscalationChainResponse;
  onClose: () => void;
}) {
  const toast = useToast();
  const [range, setRange] = useState<CalendarRange>("7d");
  const [start, setStart] = useState(todayIsoDate);
  const [data, setData] = useState<EscalationCalendarResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(
    async (nextRange = range, nextStart = start) => {
      setLoading(true);
      try {
        const resp = await getEscalationChainCalendar(chain.id, {
          range: nextRange,
          start: nextStart,
        });
        setData(resp);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [chain.id, range, start, toast],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setWindow = (nextRange: CalendarRange) => {
    const nextStart = todayIsoDate();
    setRange(nextRange);
    setStart(nextStart);
    void refresh(nextRange, nextStart);
  };

  return (
    <Modal
      open={true}
      onClose={onClose}
      title={`Escalation Calendar — ${chain.name}`}
      maxWidth="max-w-5xl"
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2">
          <p className="text-sm text-fg-primary">
            This shows who will be contacted at each escalation level for this chain.
          </p>
          <p className="mt-1 text-xs text-fg-muted">
            Coverage is resolved from escalation levels, rosters, rotation order, and active users.
          </p>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-1 rounded-md border border-border-subtle bg-bg-base p-1">
            {CALENDAR_RANGES.map((item) => (
              <Button
                key={item.value}
                size="sm"
                variant={range === item.value ? "primary" : "ghost"}
                onClick={() => setWindow(item.value)}
              >
                {item.label}
              </Button>
            ))}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setWindow(range)}
            title="Jump back to today"
          >
            <CalendarDays className="h-4 w-4" /> Today
          </Button>
        </div>

        {loading && (
          <div className="rounded-lg border border-border-subtle bg-bg-base px-4 py-6 text-sm text-fg-secondary">
            Loading escalation coverage...
          </div>
        )}

        {!loading && data && data.days.length === 0 && (
          <div className="rounded-lg border border-dashed border-border-subtle bg-bg-base px-4 py-6 text-sm text-fg-secondary">
            No calendar days returned for this range.
          </div>
        )}

        {!loading && data && data.days.length > 0 && (
          <div className="max-h-[65vh] space-y-2 overflow-y-auto pr-1">
            {data.days.map((day) => (
              <div
                key={day.date}
                className="rounded-lg border border-border-subtle bg-bg-base px-3 py-3"
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-fg-primary">
                    {fmtCalendarDate(day.date)}
                  </h3>
                  <span className="text-xs text-fg-muted">{day.date}</span>
                </div>

                {day.levels.length === 0 ? (
                  <div className="rounded-md border border-dashed border-border-subtle px-3 py-3 text-xs text-fg-secondary">
                    This chain has no escalation levels.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {day.levels.map((level) => (
                      <div
                        key={`${day.date}-${level.level}`}
                        className="grid gap-2 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-sm md:grid-cols-[5rem_7rem_1fr_1fr_7rem]"
                      >
                        <div className="font-medium text-fg-primary">
                          Level {level.level}
                        </div>
                        <div className="text-xs text-fg-muted">
                          {targetTypeLabel(level.target_type)}
                        </div>
                        <div>
                          <div className="text-fg-primary">{level.target_name}</div>
                          <div className="text-xs text-fg-muted">
                            {level.coverage_start && level.coverage_end
                              ? `${level.coverage_start}-${level.coverage_end}`
                              : "Always"}
                          </div>
                        </div>
                        <div>
                          <div className="text-fg-primary">
                            {level.resolved_user_name ?? "Unassigned"}
                          </div>
                          {level.resolved_user_email && (
                            <div className="truncate text-xs text-fg-muted">
                              {level.resolved_user_email}
                            </div>
                          )}
                          {level.warnings.length > 0 && (
                            <div className="mt-1 space-y-0.5 text-xs text-status-high">
                              {level.warnings.map((warning) => (
                                <div key={warning}>{warning}</div>
                              ))}
                            </div>
                          )}
                        </div>
                        <div className="flex items-start justify-start md:justify-end">
                          <Badge variant={calendarStatusVariant(level.status)}>
                            {calendarStatusLabel(level.status)}
                          </Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {!loading && !data && (
          <div className="rounded-lg border border-border-subtle bg-bg-base px-4 py-6 text-sm text-fg-secondary">
            Calendar coverage could not be loaded.
          </div>
        )}
      </div>
    </Modal>
  );
}

/**
 * Sprint 60 — format a cumulative-seconds value as a short timeline
 * label (e.g. `0` / `5m` / `15m` / `1h 5m` / `2h`). Used by the
 * escalation chain preview timeline.
 */
function fmtCumulativeTime(seconds: number): string {
  if (seconds === 0) return "0";
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return remMins > 0 ? `${hours}h ${remMins}m` : `${hours}h`;
}

function StepsEditor({
  chainId,
  steps,
  rosters,
  teams,
  users,
  onChange,
}: {
  chainId: string;
  steps: EscalationStepResponse[];
  rosters: RosterResponse[];
  teams: TeamResponse[];
  users: UserResponse[];
  onChange: () => Promise<void>;
}) {
  const toast = useToast();
  const [stepForm, setStepForm] = useState({
    target_type: "roster" as EscalationTargetType,
    target_id: "",
    timeout_seconds: 300,
  });
  // v1 escalation targets a roster or a user. Team-level targets are
  // deferred to v1.1, so the picker only offers roster + user here even
  // though the backend type still allows "team".
  const userTargets = useMemo(
    () =>
      users
        .filter(
          (u) =>
            u.is_active &&
            !u.deleted_at &&
            (u.role === "admin" || u.role === "operator"),
        )
        .map((u) => ({ id: u.id, name: u.username })),
    [users],
  );
  const labelForTarget = useCallback(
    (targetType: EscalationTargetType, targetId: string): string => {
      if (targetType === "roster") {
        return rosters.find((r) => r.id === targetId)?.name ?? targetId;
      }
      if (targetType === "user") {
        return users.find((u) => u.id === targetId)?.username ?? targetId;
      }
      return teams.find((t) => t.id === targetId)?.name ?? targetId;
    },
    [rosters, users, teams],
  );
  const [whereUsed, setWhereUsed] = useState<ChainWhereUsedItem[]>([]);
  const [whereLoading, setWhereLoading] = useState(false);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);

  // Load "Where used" once on mount + after onChange callbacks at the chain
  // level (link/unlink ops aren't done in this editor, but staying defensive).
  useEffect(() => {
    let cancelled = false;
    setWhereLoading(true);
    listChainServices(chainId)
      .then((res) => {
        if (!cancelled) setWhereUsed(res.items);
      })
      .catch(() => {
        // Non-fatal — operators don't have to see where-used; keep silent.
      })
      .finally(() => {
        if (!cancelled) setWhereLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [chainId]);

  const addStep = async () => {
    if (!stepForm.target_id) {
      toast.error("Pick a target");
      return;
    }
    try {
      const nextIndex =
        steps.length === 0
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

  const patchTimeout = async (id: string, seconds: number) => {
    const clamped = Math.max(10, Math.min(86400, Math.round(seconds)));
    const existing = steps.find((s) => s.id === id);
    if (existing && existing.timeout_seconds === clamped) return;
    try {
      await updateEscalationStep(chainId, id, { timeout_seconds: clamped });
      await onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const reorder = async (orderedIds: string[]) => {
    // Optimistic: caller already shows the new order via local state; just
    // persist.
    try {
      await reorderEscalationSteps(chainId, orderedIds);
      await onChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
      await onChange(); // reset to server state on failure
    }
  };

  const handleDrop = async (targetId: string) => {
    if (!draggedId || draggedId === targetId) {
      setDraggedId(null);
      setDragOverId(null);
      return;
    }
    const ordered = steps.map((s) => s.id);
    const from = ordered.indexOf(draggedId);
    const to = ordered.indexOf(targetId);
    if (from < 0 || to < 0) {
      setDraggedId(null);
      setDragOverId(null);
      return;
    }
    const [moved] = ordered.splice(from, 1);
    ordered.splice(to, 0, moved);
    setDraggedId(null);
    setDragOverId(null);
    await reorder(ordered);
  };

  const targetOptions: { id: string; name: string }[] =
    stepForm.target_type === "roster"
      ? rosters.map((r) => ({ id: r.id, name: r.name }))
      : stepForm.target_type === "user"
        ? userTargets
        : [];

  return (
    <div className="mt-3 space-y-3 rounded border border-border-default bg-bg-elevated p-3">
      {/* Where used */}
      <div className="space-y-1">
        <div className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
          Where this chain is used
        </div>
        {whereLoading ? (
          <div className="text-xs text-fg-tertiary">Loading…</div>
        ) : whereUsed.length === 0 ? (
          <div className="text-xs text-fg-tertiary">
            Not attached to any service yet. Link this chain to a service from
            the Services tab → service detail.
          </div>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {whereUsed.map((w) => (
              <li
                key={w.service_id}
                className="inline-flex items-center gap-1.5 rounded-full border border-border-default bg-bg-surface px-2.5 py-0.5 text-xs"
              >
                <span className="font-medium text-fg-primary">
                  {w.service_name}
                </span>
                {w.team_name && (
                  <span className="text-fg-muted">· {w.team_name}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Preview timeline — Sprint 60 (UX-direction Sprint D finish).
          Renders the steps as cumulative T+X events so operators can
          tell at a glance who gets paged and when. Pure presentation
          on top of the steps array; no extra fetches. */}
      {steps.length > 0 && (
        <div className="rounded-lg border border-border-subtle bg-bg-elevated/60 px-3 py-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
              Preview timeline
            </p>
            <span className="text-[10px] text-fg-muted">
              Cumulative time from incident open
            </span>
          </div>
          <ol className="space-y-1">
            {steps
              .slice()
              .sort((a, b) => a.step_index - b.step_index)
              .map((s, idx, sorted) => {
                // Cumulative time: step 0 fires at T+0; each subsequent
                // step fires after the prior step's timeout. The doc's
                // model is "additive — once paged, stay paged."
                const cumulativeSec = sorted
                  .slice(0, idx)
                  .reduce((sum, prev) => sum + prev.timeout_seconds, 0);
                const targetLabel = labelForTarget(s.target_type, s.target_id);
                return (
                  <li
                    key={`preview-${s.id}`}
                    className="flex items-center gap-3 text-xs"
                  >
                    <span className="inline-flex w-16 shrink-0 justify-end font-mono tabular-nums text-fg-secondary">
                      T+{fmtCumulativeTime(cumulativeSec)}
                    </span>
                    <span className="h-2 w-2 shrink-0 rounded-full bg-accent" />
                    <span className="text-fg-secondary">
                      <span className="font-medium text-fg-primary">
                        Level {idx + 1}
                      </span>{" "}
                      · Page{" "}
                      <span className="font-medium text-fg-primary">{targetLabel}</span>{" "}
                      <span className="text-fg-muted">({s.target_type})</span>
                    </span>
                  </li>
                );
              })}
            {/* Exhaustion row — shows the last step's timeout as the
                "no more steps fire after this" cutoff. */}
            <li className="flex items-center gap-3 pt-1 text-xs">
              <span className="inline-flex w-16 shrink-0 justify-end font-mono tabular-nums text-fg-muted">
                T+
                {fmtCumulativeTime(
                  steps.reduce((sum, s) => sum + s.timeout_seconds, 0),
                )}
              </span>
              <span className="h-2 w-2 shrink-0 rounded-full border border-border-subtle bg-transparent" />
              <span className="text-fg-muted">
                Escalation exhausted (no further steps)
              </span>
            </li>
          </ol>
        </div>
      )}

      <div className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
        Levels (additive — once paged, stay paged)
      </div>
      {steps.length === 0 ? (
        <div className="rounded-md border border-status-medium-border bg-status-medium-bg px-3 py-2 text-xs text-status-medium">
          No levels yet — this chain pages no one. Add at least Level 1 below.
        </div>
      ) : (
        <ol className="space-y-1">
          {steps.map((s, idx) => {
            const label = labelForTarget(s.target_type, s.target_id);
            const isDragging = draggedId === s.id;
            const isOver = dragOverId === s.id && draggedId !== s.id;
            return (
              <li
                key={s.id}
                draggable
                onDragStart={(e) => {
                  setDraggedId(s.id);
                  e.dataTransfer.effectAllowed = "move";
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  if (dragOverId !== s.id) setDragOverId(s.id);
                }}
                onDragLeave={() => {
                  if (dragOverId === s.id) setDragOverId(null);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  void handleDrop(s.id);
                }}
                onDragEnd={() => {
                  setDraggedId(null);
                  setDragOverId(null);
                }}
                className={`flex items-center justify-between gap-2 rounded-md border px-2 py-1.5 text-sm transition ${
                  isDragging
                    ? "border-accent bg-bg-surface opacity-60"
                    : isOver
                      ? "border-accent bg-accent-bg"
                      : "border-border-subtle bg-bg-surface"
                }`}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span
                    className="cursor-grab select-none text-fg-muted active:cursor-grabbing"
                    aria-label="Drag to reorder"
                    title="Drag to reorder"
                  >
                    ⋮⋮
                  </span>
                  <Badge variant="default">Level {idx + 1}</Badge>
                  <span className="text-fg-secondary">{s.target_type}</span>
                  <span className="truncate font-medium text-fg-primary">
                    {label}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <label className="flex items-center gap-1 text-[11px] text-fg-tertiary">
                    timeout
                    <Input
                      type="number"
                      min={10}
                      max={86400}
                      defaultValue={s.timeout_seconds}
                      onBlur={(e) =>
                        patchTimeout(s.id, Number(e.target.value) || 300)
                      }
                      onKeyDown={(e) => {
                        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                      }}
                      className="w-20"
                    />
                    s
                  </label>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeStep(s.id)}
                    title="Delete step"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </li>
            );
          })}
        </ol>
      )}

      <div className="grid grid-cols-1 items-end gap-2 pt-2 sm:grid-cols-[120px_1fr_120px_auto]">
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
            <option value="user">User</option>
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
        <Button onClick={addStep}>Add level</Button>
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
  const stages = [
    {
      number: "1",
      heading: "Triage",
      question: "How urgent is it?",
      tone: "info" as const,
      lines: [
        "Service configuration sets P0–P3",
        "Priority controls whether OpsMender resolves, notifies, or pages",
      ],
    },
    {
      number: "2",
      heading: "Route",
      question: "Who owns this?",
      tone: "accent" as const,
      lines: [
        "The Service owns one Team",
        "The Team’s Escalation Chain takes over",
      ],
    },
    {
      number: "3",
      heading: "Page",
      question: "Who wakes up — and when?",
      tone: "warn" as const,
      lines: [
        "Chain fires steps additively on a timeout",
        "Roster resolves who’s on-call right now",
        "Ack pauses the chain · 15-min hard cap",
      ],
    },
  ];

  const toneStyles: Record<string, string> = {
    info: "border-status-info-border bg-status-info-bg text-status-info",
    accent: "border-accent bg-accent-bg text-accent",
    warn: "border-status-medium-border bg-status-medium-bg text-status-medium",
  };

  return (
    <Modal open={open} onClose={onClose} title="How paging works">
      <div className="space-y-5">
        <p className="text-sm text-fg-secondary">
          Every incident flows through the same three stops. The first decides
          how urgent it is. The second decides who owns it. The third decides
          who actually gets paged.
        </p>

        <div className="rounded-xl border border-border-default bg-bg-elevated p-5">
          <ol className="grid gap-4 md:grid-cols-3">
            {stages.map((stage, i) => (
              <li key={stage.number} className="relative flex flex-col">
                <div className="flex items-center gap-3">
                  <div
                    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-base font-bold ${toneStyles[stage.tone]}`}
                  >
                    {stage.number}
                  </div>
                  <div>
                    <div className="text-base font-semibold text-fg-primary">
                      {stage.heading}
                    </div>
                    <div className="text-xs text-fg-tertiary">
                      {stage.question}
                    </div>
                  </div>
                </div>
                <ul className="mt-3 space-y-1.5 text-sm text-fg-secondary">
                  {stage.lines.map((line) => (
                    <li key={line} className="flex gap-2">
                      <span className="text-fg-muted">·</span>
                      <span>{line}</span>
                    </li>
                  ))}
                </ul>
                {i < stages.length - 1 && (
                  <div
                    aria-hidden
                    className="hidden md:block absolute -right-2 top-4 text-fg-muted"
                  >
                    →
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>

        <div className="grid gap-2 sm:grid-cols-2 text-xs text-fg-secondary">
          <div className="rounded-md border border-border-subtle bg-bg-surface px-3 py-2">
            <span className="font-semibold text-fg-primary">
              Escalation is additive.
            </span>{" "}
            Later steps add people; they don’t replace earlier ones.
          </div>
          <div className="rounded-md border border-border-subtle bg-bg-surface px-3 py-2">
            <span className="font-semibold text-fg-primary">
              Service priority is locked.
            </span>{" "}
            AI can assist, but it does not override priority in v1.
          </div>
          <div className="rounded-md border border-border-subtle bg-bg-surface px-3 py-2">
            <span className="font-semibold text-fg-primary">
              Operators stay in control.
            </span>{" "}
            Admins choose priorities on services before alerts arrive.
          </div>
          <div className="rounded-md border border-border-subtle bg-bg-surface px-3 py-2">
            <span className="font-semibold text-fg-primary">
              Incident-scoped authority.
            </span>{" "}
            The active assignee gets operator rights on that incident.
          </div>
        </div>

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

type MaintenanceStatus = "active" | "scheduled" | "past";

function MaintenanceWindowsPanel({
  windows,
  services,
  rosters,
  teams,
  onChange,
  canEdit,
}: {
  windows: MaintenanceWindowResponse[];
  services: ServiceResponse[];
  rosters: RosterResponse[];
  teams: TeamResponse[];
  onChange: () => void;
  canEdit?: boolean;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<MaintenanceWindowResponse | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [scopeFilter, setScopeFilter] = useState<string[]>([]);
  const deferredSearch = useDeferredValue(search);
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
  const blankForm = () => ({
    name: "",
    description: "",
    reason: "",
    starts_at: nowMinusOne(),
    ends_at: nowPlusTwo(),
    scope_type: "global" as MaintenanceWindowScopeType,
    scope_ids: [] as string[],
  });
  const [form, setForm] = useState(blankForm);

  const statusOf = (w: MaintenanceWindowResponse): MaintenanceStatus => {
    const now = new Date();
    if (new Date(w.starts_at) > now) return "scheduled";
    if (new Date(w.ends_at) <= now) return "past";
    return "active";
  };

  const scopeOptionsFor = (
    type: MaintenanceWindowScopeType,
  ): { id: string; name: string }[] => {
    if (type === "service") return services.map((s) => ({ id: s.id, name: s.name }));
    if (type === "roster") return rosters.map((r) => ({ id: r.id, name: r.name }));
    if (type === "team") return teams.map((t) => ({ id: t.id, name: t.name }));
    return [];
  };

  const scopeIdsOf = (w: MaintenanceWindowResponse): string[] =>
    w.scope_ids?.length ? w.scope_ids : w.scope_id ? [w.scope_id] : [];

  const scopeLabelFor = (w: MaintenanceWindowResponse): string => {
    if (w.scope_type === "global") return "Global · all paging";
    const opts = scopeOptionsFor(w.scope_type);
    const names = scopeIdsOf(w).map(
      (id) => opts.find((o) => o.id === id)?.name ?? id,
    );
    return `${w.scope_type}: ${names.join(", ") || "?"}`;
  };

  const openCreate = () => {
    setEditing(null);
    setForm(blankForm());
    setOpen(true);
  };

  const openEdit = (w: MaintenanceWindowResponse) => {
    setEditing(w);
    setForm({
      name: w.name,
      description: w.description ?? "",
      reason: w.reason ?? "",
      starts_at: toLocalInput(w.starts_at),
      ends_at: toLocalInput(w.ends_at),
      scope_type: w.scope_type,
      scope_ids: scopeIdsOf(w),
    });
    setOpen(true);
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
    if (form.scope_type !== "global" && form.scope_ids.length === 0) {
      toast.error("Pick at least one scope target");
      return;
    }
    const payload = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      reason: form.reason.trim() || null,
      starts_at: new Date(form.starts_at).toISOString(),
      ends_at: new Date(form.ends_at).toISOString(),
      scope_type: form.scope_type,
      scope_id: form.scope_type === "global" ? null : form.scope_ids[0],
      scope_ids: form.scope_type === "global" ? [] : form.scope_ids,
    };
    try {
      if (editing) {
        await updateMaintenanceWindow(editing.id, payload);
      } else {
        await createMaintenanceWindow(payload);
      }
      setOpen(false);
      setEditing(null);
      setForm(blankForm());
      onChange();
      toast.success(editing ? "Maintenance window updated" : "Maintenance window scheduled");
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

  const filteredWindows = useMemo(() => {
    const q = deferredSearch.trim().toLowerCase();
    return windows.filter((w) => {
      if (q) {
        const haystack = [w.name, w.description ?? "", scopeLabelFor(w)]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      if (statusFilter.length && !statusFilter.includes(statusOf(w)))
        return false;
      if (scopeFilter.length && !scopeFilter.includes(w.scope_type))
        return false;
      return true;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windows, deferredSearch, statusFilter, scopeFilter]);

  const columns = useMemo<DataTableColumn<MaintenanceWindowResponse>[]>(
    () => [
      {
        id: "name",
        label: "Name",
        accessor: (w) => w.name,
        sortable: true,
        cell: (w) => (
          <div className="flex items-center gap-2">
            <Wrench className="h-4 w-4 text-fg-secondary" />
            <div>
              <div className="font-medium text-fg-primary">{w.name}</div>
              {w.description && (
                <div className="line-clamp-1 text-[11px] text-fg-muted">
                  {w.description}
                </div>
              )}
            </div>
          </div>
        ),
      },
      {
        id: "scope",
        label: "Scope",
        accessor: (w) => scopeLabelFor(w),
        cell: (w) => <Badge variant="default">{scopeLabelFor(w)}</Badge>,
      },
      {
        id: "starts_at",
        label: "Starts at",
        accessor: (w) => w.starts_at,
        sortable: true,
        cell: (w) => (
          <span className="whitespace-nowrap text-xs text-fg-secondary">
            {new Date(w.starts_at).toLocaleString()}
          </span>
        ),
      },
      {
        id: "ends_at",
        label: "Ends at",
        accessor: (w) => w.ends_at,
        sortable: true,
        cell: (w) => (
          <span className="whitespace-nowrap text-xs text-fg-secondary">
            {new Date(w.ends_at).toLocaleString()}
          </span>
        ),
      },
      {
        id: "status",
        label: "Status",
        accessor: (w) => (!w.approved ? "pending approval" : statusOf(w)),
        sortable: true,
        cell: (w) => {
          if (!w.approved) {
            return (
              <div className="flex flex-col gap-1">
                <Badge variant="medium">pending approval</Badge>
                <span className="text-[10px] text-fg-muted">
                  Does not suppress alerts until approved
                </span>
              </div>
            );
          }
          const s = statusOf(w);
          return (
            <Badge variant={s === "active" ? "open" : s === "scheduled" ? "info" : "closed"}>
              {s}
            </Badge>
          );
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [services, rosters, teams],
  );

  const hasFilters = Boolean(
    search || statusFilter.length || scopeFilter.length,
  );

  return (
    <section className="space-y-3">
      {windows.length === 0 ? (
        <EmptyState
          title="No maintenance windows"
          description="Schedule a window to drop matching alerts during planned work before they create visible incidents."
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/paging-guide.md"
          learnMoreLabel="Paging guide"
          action={
            <Button onClick={openCreate}>
              <PlusCircle className="h-4 w-4" />
              {canEdit ? "Schedule window" : "Request window"}
            </Button>
          }
        />
      ) : (
        <>
          <PagingFilterBar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search maintenance windows..."
            searchAriaLabel="Search maintenance windows"
            filters={[
              {
                id: "status",
                label: "statuses",
                values: statusFilter,
                onChange: setStatusFilter,
                options: [
                  { value: "active", label: "Active" },
                  { value: "scheduled", label: "Scheduled" },
                  { value: "past", label: "Past" },
                ],
              },
              {
                id: "scope",
                label: "scopes",
                values: scopeFilter,
                onChange: setScopeFilter,
                options: [
                  { value: "global", label: "Global" },
                  { value: "service", label: "Service" },
                  { value: "team", label: "Team" },
                  { value: "roster", label: "Roster" },
                ],
              },
            ]}
            hasFilters={hasFilters}
            onClear={() => {
              setSearch("");
              setStatusFilter([]);
              setScopeFilter([]);
            }}
            action={
              <Button size="sm" onClick={openCreate}>
                <PlusCircle className="h-4 w-4" />
                {canEdit ? "Schedule window" : "Request window"}
              </Button>
            }
          />
          <DataTable
            rows={filteredWindows}
            columns={columns}
            rowKey={(w) => w.id}
            storageKey="opsmender:maintenance-table"
            hideToolbar
            empty={
              <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
                No maintenance windows match the current filters.
              </div>
            }
            rowActions={(w) => (
              <div className="flex items-center gap-1">
                {canEdit && !w.approved && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={async () => {
                      try {
                        await approveMaintenanceWindow(w.id);
                        onChange();
                      } catch (err) {
                        toast.error(err instanceof Error ? err.message : "Approve failed");
                      }
                    }}
                    title="Approve pending request"
                  >
                    Approve
                  </Button>
                )}
                {canEdit && !w.approved && (
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={async () => {
                      try {
                        await rejectMaintenanceWindow(w.id);
                        onChange();
                      } catch (err) {
                        toast.error(err instanceof Error ? err.message : "Reject failed");
                      }
                    }}
                    title="Reject pending request"
                  >
                    Reject
                  </Button>
                )}
                {canEdit && w.approved && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => openEdit(w)}
                    title="Edit window"
                  >
                    <Pencil className="h-4 w-4" /> Edit
                  </Button>
                )}
                {canEdit && (
                  <Button variant="ghost" size="sm" onClick={() => remove(w.id)} title="Delete">
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            )}
          />
        </>
      )}

      <Modal
        open={open}
        onClose={() => {
          setOpen(false);
          setEditing(null);
        }}
        title={editing ? "Edit maintenance window" : "Schedule maintenance window"}
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
          <div>
            <Label>Scope</Label>
            <Select
              value={form.scope_type}
              onChange={(e) =>
                setForm({
                  ...form,
                  scope_type: e.target.value as MaintenanceWindowScopeType,
                  scope_ids: [],
                })
              }
            >
              <option value="global">Global (all paging)</option>
              <option value="service">Service</option>
              <option value="team">Team</option>
              <option value="roster">Roster</option>
            </Select>
          </div>
          {form.scope_type !== "global" && (
            <div>
              <Label>Targets ({form.scope_type})</Label>
              <MultiSelect
                ariaLabel={`Maintenance window ${form.scope_type} targets`}
                options={scopeOptionsFor(form.scope_type).map((o) => ({
                  value: o.id,
                  label: o.name,
                }))}
                selected={form.scope_ids}
                onChange={(next) => setForm({ ...form, scope_ids: next })}
                emptyLabel={`No ${form.scope_type}s available.`}
              />
            </div>
          )}
          <p className="text-xs text-fg-tertiary">
            Matching alerts are dropped during the window so planned work does
            not create noisy visible incidents. Non-matching alerts still flow.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>{editing ? "Save" : "Schedule"}</Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}

const ALL_PRIORITIES: Priority[] = ["P0", "P1", "P2", "P3"];

type NotificationsTab = "my_routing" | "routing_summary" | "channels";

const NOTIFICATIONS_TABS: { id: NotificationsTab; label: string }[] = [
  { id: "my_routing", label: "My Routing" },
  { id: "routing_summary", label: "Routing Summary" },
  { id: "channels", label: "Notification Channels" },
];

function NotificationsPanel({
  services,
  teams,
  chains,
}: {
  services: ServiceResponse[];
  teams: TeamResponse[];
  chains: EscalationChainResponse[];
}) {
  const [subTab, setSubTab] = useState<NotificationsTab>("my_routing");

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap gap-1 rounded-md border border-border-default bg-bg-surface p-1">
        {NOTIFICATIONS_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setSubTab(t.id)}
            aria-current={subTab === t.id ? "page" : undefined}
            className={`rounded px-3 py-1.5 text-sm transition ${
              subTab === t.id
                ? "bg-bg-elevated text-fg-primary"
                : "text-fg-secondary hover:text-fg-primary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {subTab === "my_routing" && (
        <NotificationPreferencesPanel
          onGoToChannels={() => setSubTab("channels")}
        />
      )}

      {subTab === "routing_summary" && (
        <RoutingSummaryPanel
          services={services}
          teams={teams}
          chains={chains}
        />
      )}

      {subTab === "channels" && (
        <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-4">
          <h3 className="text-sm font-semibold text-fg-primary">
            Notification Channels
          </h3>
          <p className="mt-1 text-sm text-fg-secondary">
            Workspace-level channels OpsMender uses to reach people — Telegram,
            Signal, WhatsApp, Slack, Discord, Microsoft Teams, Mattermost,
            Matrix, Email, SMS, and custom adapters. Chat-capable adapters can
            also host incident sessions.
          </p>
          <div className="mt-4">
            <NotificationChannelsPage embedded />
          </div>
        </div>
      )}

    </section>
  );
}

/**
 * Read-only routing summary (v1). Derives "who gets paged" from existing
 * services → escalation chains → rosters/users → channels rather than
 * introducing an editable team-routing model. Editable team-level routing
 * defaults are planned for v1.1.
 */
function RoutingSummaryPanel({
  services,
  teams,
  chains,
}: {
  services: ServiceResponse[];
  teams: TeamResponse[];
  chains: EscalationChainResponse[];
}) {
  const [chainByService, setChainByService] = useState<Map<string, string>>(
    new Map(),
  );

  const teamNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of teams) map.set(t.id, t.name);
    return map;
  }, [teams]);
  const chainNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of chains) map.set(c.id, c.name);
    return map;
  }, [chains]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(
        services.map(async (s) => {
          try {
            const links = await listServiceEscalationChains(s.id);
            return [s.id, links.items[0]?.chain_id ?? ""] as const;
          } catch {
            return [s.id, ""] as const;
          }
        }),
      );
      if (!cancelled) setChainByService(new Map(entries));
    })();
    return () => {
      cancelled = true;
    };
  }, [services]);

  const RESPONSE_BY_PRIORITY: Record<Priority, string> = {
    P0: "Page on-call",
    P1: "Page on-call",
    P2: "Notify",
    P3: "Auto-resolve",
  };

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-secondary">
        Routing is derived from services, escalation chains, rosters, and
        notification channels. Editable team-level routing defaults are planned
        for v1.1.
      </div>

      <div className="overflow-x-auto rounded-lg border border-border-default bg-bg-surface">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-border-default text-xs text-fg-secondary">
              <th className="px-3 py-2 text-left font-medium">Service</th>
              <th className="px-3 py-2 text-left font-medium">Priority</th>
              <th className="px-3 py-2 text-left font-medium">Response</th>
              <th className="px-3 py-2 text-left font-medium">Team</th>
              <th className="px-3 py-2 text-left font-medium">Escalation chain</th>
            </tr>
          </thead>
          <tbody>
            {services.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-fg-muted">
                  No services yet. Add a service to define routing.
                </td>
              </tr>
            ) : (
              services.map((s) => {
                const chainId = chainByService.get(s.id) ?? "";
                return (
                  <tr key={s.id} className="border-b border-border-default last:border-0">
                    <td className="px-3 py-2 font-medium text-fg-primary">
                      {s.name}
                    </td>
                    <td className="px-3 py-2">
                      <Badge variant={PRIORITY_VARIANT[s.priority] as never}>
                        {s.priority}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-fg-secondary">
                      {RESPONSE_BY_PRIORITY[s.priority]}
                    </td>
                    <td className="px-3 py-2 text-fg-secondary">
                      {teamNameById.get(s.team_id) ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-fg-secondary">
                      {chainId ? (
                        chainNameById.get(chainId) ?? "linked chain"
                      ) : (
                        <span className="text-fg-muted">— none —</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <ul className="space-y-1 text-xs text-fg-tertiary">
        <li>· P0/P1 page through the service&apos;s escalation chain.</li>
        <li>· Each level targets a roster or user; the roster resolves the current Admin/Operator from its coverage window and rotation order.</li>
        <li>· Delivery uses each recipient&apos;s configured notification channels.</li>
      </ul>
    </div>
  );
}

const PRIORITY_META: Record<Priority, { label: string; description: string }> = {
  P0: { label: "Critical", description: "Highest urgency" },
  P1: { label: "High", description: "High priority" },
  P2: { label: "Medium", description: "Medium priority" },
  P3: { label: "Low", description: "Low priority" },
};

// Days of week for quiet hours, matching Python datetime.weekday() (Mon=0).
const QUIET_DAYS: { value: number; label: string }[] = [
  { value: 0, label: "Mon" },
  { value: 1, label: "Tue" },
  { value: 2, label: "Wed" },
  { value: 3, label: "Thu" },
  { value: 4, label: "Fri" },
  { value: 5, label: "Sat" },
  { value: 6, label: "Sun" },
];

// Friendly labels for legacy delivery keys still present in older prefs.
const LEGACY_CHANNEL_LABELS: Record<string, string> = {
  slack_dm: "Slack DM",
  teams_dm: "Teams DM",
  teams_dm_graph: "Teams DM",
  email: "Email",
  sms: "SMS",
};

// Stage delay options gating the next escalation stage.
const STAGE_DELAY_OPTIONS: { value: number; label: string }[] = [
  { value: 60, label: "1 min" },
  { value: 120, label: "2 min" },
  { value: 300, label: "5 min" },
  { value: 600, label: "10 min" },
  { value: 900, label: "15 min" },
  { value: 1800, label: "30 min" },
];

/** Normalize a priority's routing into ordered stages (new + legacy shape). */
function normalizeRoutingStages(raw: unknown): RoutingStage[] {
  if (!Array.isArray(raw)) return [];
  const out: RoutingStage[] = [];
  for (const entry of raw) {
    if (typeof entry === "string") {
      if (entry.trim()) out.push({ channel_id: entry, delay_seconds: 0 });
    } else if (entry && typeof entry === "object") {
      const channel_id = String(
        (entry as RoutingStage).channel_id ?? "",
      ).trim();
      if (!channel_id) continue;
      const delay = Number((entry as RoutingStage).delay_seconds ?? 300);
      out.push({ channel_id, delay_seconds: Number.isFinite(delay) ? delay : 300 });
    }
    if (out.length >= 3) break;
  }
  return out;
}

/** Checkbox dropdown (popover) for selecting channels — no Ctrl/Cmd. */
export function ChannelMultiSelect({
  options,
  selected,
  onToggle,
}: {
  options: { key: NotificationChannelKey; label: string }[];
  selected: NotificationChannelKey[];
  onToggle: (key: NotificationChannelKey, on: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const label =
    selected.length > 0
      ? `Selected channels (${selected.length})`
      : "Do not notify";

  return (
    <div ref={ref} className="relative w-56">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-border-strong bg-bg-input px-3 text-sm text-fg-primary"
      >
        <span className="truncate">{label}</span>
        <ChevronDown size={15} className="shrink-0 text-fg-muted" />
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-full rounded-md border border-border-strong bg-bg-elevated p-1 shadow-lg">
          {options.length === 0 ? (
            <p className="px-2 py-2 text-xs text-fg-muted">
              No channels available.
            </p>
          ) : (
            options.map((o) => (
              <label
                key={o.key}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-bg-hover"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(o.key)}
                  onChange={(e) => onToggle(o.key, e.target.checked)}
                />
                <span>{o.label}</span>
              </label>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export function NotificationPreferencesPanel({
  onGoToChannels,
}: {
  onGoToChannels?: () => void;
}) {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [pref, setPref] = useState<UserNotificationPrefResponse | null>(null);
  const [channels, setChannels] = useState<
    Record<string, Record<string, string>>
  >({});
  // Ordered escalation stages per priority.
  const [routing, setRouting] = useState<Record<string, RoutingStage[]>>({});
  const [botConnectors, setBotConnectors] = useState<BotConnectorResponse[]>([]);
  const [quietEnabled, setQuietEnabled] = useState(false);
  const [quiet, setQuiet] = useState<{
    weekday_start: string;
    weekday_end: string;
    time_zone: string;
  }>({ weekday_start: "22:00", weekday_end: "07:00", time_zone: "UTC" });
  const [quietDays, setQuietDays] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]);

  // Routing channels are driven entirely by configured Notification Channels
  // (enabled connectors). No hardcoded delivery list — a new connector becomes
  // routable automatically. Friendly names only; provider lives in channel
  // config.
  const channelOptions = useMemo(
    () =>
      botConnectors
        .filter((c) => c.is_enabled)
        .map((c) => ({ value: c.id, label: c.name })),
    [botConnectors],
  );

  const channelLabel = useCallback(
    (channelId: string) =>
      botConnectors.find((c) => c.id === channelId)?.name ??
      LEGACY_CHANNEL_LABELS[channelId] ??
      channelId,
    [botConnectors],
  );

  useEffect(() => {
    (async () => {
      try {
        const [data, connectors] = await Promise.all([
          getMyNotificationPreferences(),
          listBotConnectors().catch(() => ({
            items: [] as BotConnectorResponse[],
            total: 0,
          })),
        ]);
        setPref(data);
        setChannels(data.channels ?? {});
        const normalized: Record<string, RoutingStage[]> = {};
        for (const [priority, raw] of Object.entries(data.routing ?? {})) {
          normalized[priority] = normalizeRoutingStages(raw);
        }
        setRouting(normalized);
        setBotConnectors(connectors.items);
        const qh = data.quiet_hours;
        const start = qh?.weekday_start ?? qh?.weekday?.start;
        const end = qh?.weekday_end ?? qh?.weekday?.end;
        if (qh && start && end) {
          setQuietEnabled(true);
          setQuiet({
            weekday_start: start,
            weekday_end: end,
            time_zone: qh.time_zone ?? "UTC",
          });
          setQuietDays(
            Array.isArray(qh.days) && qh.days.length > 0
              ? qh.days
              : [0, 1, 2, 3, 4, 5, 6],
          );
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateStages = (
    priority: Priority,
    fn: (stages: RoutingStage[]) => RoutingStage[],
  ) => {
    setRouting((prev) => ({ ...prev, [priority]: fn(prev[priority] ?? []) }));
  };

  const addStage = (priority: Priority) => {
    updateStages(priority, (stages) => {
      if (stages.length >= 3) return stages;
      const channel_id = channelOptions[0]?.value ?? "";
      return [...stages, { channel_id, delay_seconds: 300 }];
    });
  };

  const removeStage = (priority: Priority, idx: number) => {
    updateStages(priority, (stages) => stages.filter((_, i) => i !== idx));
  };

  const moveStage = (priority: Priority, idx: number, dir: -1 | 1) => {
    updateStages(priority, (stages) => {
      const next = idx + dir;
      if (next < 0 || next >= stages.length) return stages;
      const copy = stages.slice();
      [copy[idx], copy[next]] = [copy[next], copy[idx]];
      return copy;
    });
  };

  const setStageChannel = (priority: Priority, idx: number, channel_id: string) => {
    updateStages(priority, (stages) =>
      stages.map((s, i) => (i === idx ? { ...s, channel_id } : s)),
    );
  };

  const setStageDelay = (priority: Priority, idx: number, delay_seconds: number) => {
    updateStages(priority, (stages) =>
      stages.map((s, i) => (i === idx ? { ...s, delay_seconds } : s)),
    );
  };

  const toggleDay = (day: number) => {
    setQuietDays((prev) =>
      prev.includes(day)
        ? prev.filter((d) => d !== day)
        : [...prev, day].sort((a, b) => a - b),
    );
  };

  const save = async () => {
    setSaving(true);
    try {
      const quiet_hours: QuietHoursConfig | null = quietEnabled
        ? {
            weekday_start: quiet.weekday_start,
            weekday_end: quiet.weekday_end,
            days: quietDays,
            // P0 always pages through; only P1-P3 are subject to quiet hours.
            min_priority_to_break: "P0",
            time_zone: quiet.time_zone || "UTC",
          }
        : null;
      const routingPayload: Record<
        string,
        { channel_id: string; delay_seconds: number }[]
      > = {};
      for (const [priority, stages] of Object.entries(routing)) {
        routingPayload[priority] = stages
          .filter((s) => s.channel_id)
          .map((s) => ({
            channel_id: s.channel_id,
            delay_seconds: s.delay_seconds,
          }));
      }
      const updated = await updateMyNotificationPreferences({
        channels,
        routing: routingPayload,
        quiet_hours,
      });
      setPref(updated);
      toast.success("Routing saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    try {
      const res = await testMyNotificationPreferences();
      if (res.tested === 0) {
        toast.success(
          "No channels to test yet — add a channel to a priority first.",
        );
      } else {
        const sent = res.results.filter((r) => r.status === "sent").length;
        const skipped = res.results.filter((r) => r.status === "skipped").length;
        const failed = res.results.filter((r) => r.status === "failed").length;
        toast.success(
          `Test notification: ${sent} delivered, ${skipped} skipped, ${failed} failed.`,
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <p className="text-sm text-fg-secondary">Loading your preferences…</p>
    );
  }

  const noChannels = botConnectors.length === 0;

  return (
    <section className="space-y-6">
      {/* Header + Test notification */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-fg-primary">
            Routing by Priority
          </h2>
          <p className="text-sm text-fg-secondary">
            Choose how you want to be notified for each incident priority.
            Unselected priorities are skipped.
          </p>
        </div>
        <Button variant="secondary" onClick={runTest} loading={testing}>
          <Send className="h-4 w-4" /> Test notification
        </Button>
      </div>

      {noChannels && (
        <div className="rounded-lg border border-status-info-border bg-status-info-bg px-4 py-3">
          <p className="text-sm font-medium text-fg-primary">
            No notification channels are configured yet
          </p>
          <p className="mt-1 text-sm text-fg-secondary">
            Connect a channel so OpsMender can deliver pages. Configure them in
            the Notification Channels tab.
          </p>
          {onGoToChannels && (
            <Button
              variant="secondary"
              size="sm"
              className="mt-2"
              onClick={onGoToChannels}
            >
              Go to Notification Channels
            </Button>
          )}
        </div>
      )}

      {/* Priority rows — ordered escalation stages */}
      <div className="space-y-3">
        {ALL_PRIORITIES.map((p) => {
          const meta = PRIORITY_META[p];
          const stages = routing[p] ?? [];
          return (
            <div
              key={p}
              className="space-y-3 rounded-lg border border-border-default bg-bg-surface p-4"
            >
              <div className="flex flex-wrap items-center gap-3">
                <Badge variant={PRIORITY_VARIANT[p] as never}>{p}</Badge>
                <div className="min-w-[8rem]">
                  <div className="font-medium text-fg-primary">{meta.label}</div>
                  <div className="text-xs text-fg-muted">{meta.description}</div>
                </div>
                <span className="ml-auto text-xs text-fg-muted">
                  {stages.length}/3 stages
                </span>
              </div>

              {stages.length === 0 ? (
                <div className="inline-flex items-center gap-1 text-xs text-fg-muted">
                  <BellOff size={12} /> Do not notify
                </div>
              ) : (
                <ol className="space-y-2">
                  {stages.map((stage, idx) => {
                    const inOptions = channelOptions.some(
                      (o) => o.value === stage.channel_id,
                    );
                    return (
                      <li
                        key={idx}
                        className="flex flex-wrap items-center gap-2 rounded-md border border-border-subtle bg-bg-elevated p-2"
                      >
                        <span className="w-16 shrink-0 font-mono text-xs text-fg-muted">
                          Stage {idx + 1}
                        </span>
                        <Select
                          aria-label={`${p} stage ${idx + 1} channel`}
                          value={stage.channel_id}
                          onChange={(e) =>
                            setStageChannel(p, idx, e.target.value)
                          }
                          className="h-9 min-w-[12rem] flex-1"
                        >
                          {/* Surface a legacy/removed channel so it stays visible. */}
                          {!inOptions && stage.channel_id && (
                            <option value={stage.channel_id}>
                              {channelLabel(stage.channel_id)}
                            </option>
                          )}
                          {channelOptions.length === 0 && !stage.channel_id && (
                            <option value="">No channels configured</option>
                          )}
                          {channelOptions.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                        {idx < stages.length - 1 && (
                          <label className="inline-flex items-center gap-1 text-xs text-fg-muted">
                            Wait
                            <Select
                              aria-label={`${p} stage ${idx + 1} delay`}
                              value={String(stage.delay_seconds)}
                              onChange={(e) =>
                                setStageDelay(p, idx, Number(e.target.value))
                              }
                              className="h-9"
                            >
                              {STAGE_DELAY_OPTIONS.map((d) => (
                                <option key={d.value} value={d.value}>
                                  {d.label}
                                </option>
                              ))}
                            </Select>
                          </label>
                        )}
                        <div className="ml-auto flex items-center gap-0.5">
                          <button
                            type="button"
                            aria-label={`Move ${p} stage ${idx + 1} up`}
                            disabled={idx === 0}
                            onClick={() => moveStage(p, idx, -1)}
                            className="rounded p-1 text-fg-muted hover:text-fg-primary disabled:opacity-30"
                          >
                            <ChevronUp size={14} />
                          </button>
                          <button
                            type="button"
                            aria-label={`Move ${p} stage ${idx + 1} down`}
                            disabled={idx === stages.length - 1}
                            onClick={() => moveStage(p, idx, 1)}
                            className="rounded p-1 text-fg-muted hover:text-fg-primary disabled:opacity-30"
                          >
                            <ChevronDown size={14} />
                          </button>
                          <button
                            type="button"
                            aria-label={`Remove ${p} stage ${idx + 1}`}
                            onClick={() => removeStage(p, idx)}
                            className="rounded p-1 text-fg-muted hover:text-status-critical"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              )}

              <Button
                variant="secondary"
                size="sm"
                disabled={stages.length >= 3 || channelOptions.length === 0}
                onClick={() => addStage(p)}
              >
                <PlusCircle className="h-4 w-4" /> Add stage
              </Button>
            </div>
          );
        })}
      </div>

      <div className="flex items-start gap-2 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-secondary">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-fg-muted" />
        <span>
          Only configured channels will be used for routing. You can configure
          channels in{" "}
          {onGoToChannels ? (
            <button
              type="button"
              onClick={onGoToChannels}
              className="text-accent underline"
            >
              Notification Channels
            </button>
          ) : (
            "Notification Channels"
          )}
          . Chat-capable channels (Slack, Teams) can also host incident
          sessions; Email and SMS are delivery-only.
        </span>
      </div>

      {/* Quiet hours */}
      <div className="rounded-lg border border-border-default bg-bg-surface p-4">
        <h2 className="text-base font-semibold text-fg-primary">Quiet Hours</h2>
        <p className="mt-1 text-sm text-fg-secondary">
          During quiet hours, non-critical notifications are suppressed.
          Critical (P0) will still page through.
        </p>
        <label className="mt-3 inline-flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={quietEnabled}
            onChange={(e) => setQuietEnabled(e.target.checked)}
          />
          Enable quiet hours
        </label>
        {quietEnabled && (
          <div className="mt-4 space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <Label className="text-xs">Time zone (IANA)</Label>
                <Input
                  value={quiet.time_zone}
                  onChange={(e) =>
                    setQuiet({ ...quiet, time_zone: e.target.value })
                  }
                  placeholder="UTC"
                />
              </div>
              <div>
                <Label className="text-xs">Start time</Label>
                <Input
                  type="time"
                  value={quiet.weekday_start}
                  onChange={(e) =>
                    setQuiet({ ...quiet, weekday_start: e.target.value })
                  }
                />
              </div>
              <div>
                <Label className="text-xs">End time</Label>
                <Input
                  type="time"
                  value={quiet.weekday_end}
                  onChange={(e) =>
                    setQuiet({ ...quiet, weekday_end: e.target.value })
                  }
                />
              </div>
            </div>
            <div>
              <Label className="text-xs">Days</Label>
              <div className="flex flex-wrap gap-1.5">
                {QUIET_DAYS.map((d) => {
                  const on = quietDays.includes(d.value);
                  return (
                    <button
                      key={d.value}
                      type="button"
                      aria-pressed={on}
                      onClick={() => toggleDay(d.value)}
                      className={`rounded-md border px-3 py-1.5 text-xs transition ${
                        on
                          ? "border-accent bg-accent text-white"
                          : "border-border-strong bg-bg-input text-fg-secondary hover:text-fg-primary"
                      }`}
                    >
                      {d.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <p className="text-xs text-fg-muted">
              Quiet hours apply to P1, P2, and P3 only. P0 (Critical) always
              pages through.
            </p>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between">
        <div className="text-xs text-fg-tertiary">
          {pref &&
            `Routing changes apply immediately. Last updated ${new Date(
              pref.updated_at,
            ).toLocaleString()}.`}
        </div>
        <Button onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save routing"}
        </Button>
      </div>
    </section>
  );
}
