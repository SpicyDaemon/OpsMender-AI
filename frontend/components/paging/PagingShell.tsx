"use client";

import {
  type ReactNode,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import {
  Bell,
  Calendar,
  CalendarDays,
  GitBranch,
  Info,
  ListOrdered,
  PlusCircle,
  Repeat,
  Search,
  Server,
  Trash2,
  Users,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";

import { RosterCalendarModal } from "@/components/RosterCalendarModal";
import { NotificationChannelsPage } from "@/components/NotificationChannelsPage";
import { OutboundHooksPage } from "@/components/OutboundHooksPage";

import {
  createMaintenanceWindow,
  deleteMaintenanceWindow,
  listMaintenanceWindows,
} from "@/lib/api_reliability";
import {
  addEscalationStep,
  createEscalationChain,
  createRoster,
  createService,
  createTeam,
  deleteEscalationChain,
  deleteEscalationStep,
  deleteRoster,
  deleteService,
  deleteTeam,
  getMyNotificationPreferences,
  listChainServices,
  listEscalationChains,
  listEscalationSteps,
  listIncidents,
  listMCPServers,
  listRosters,
  listServices,
  listTeams,
  listUsers,
  reorderEscalationSteps,
  resolveOnCall,
  updateEscalationStep,
  updateMyNotificationPreferences,
  updateRoster,
  updateService,
} from "@/lib/api";
import type {
  ChainWhereUsedItem,
  EscalationChainResponse,
  EscalationStepResponse,
  EscalationTargetType,
  IncidentResponse,
  MaintenanceWindowResponse,
  MaintenanceWindowScopeType,
  MCPServerResponse,
  NotificationChannelKey,
  Priority,
  QuietHoursConfig,
  RosterResponse,
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
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";

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

      {tab === "teams" && <TeamsPanel teams={teams} onChange={refresh} />}
      {tab === "services" && (
        <ServicesPanel
          services={services}
          teams={teams}
          rosters={rosters}
          onChange={refresh}
        />
      )}
      {tab === "rosters" && (
        <RostersPanel rosters={rosters} teams={teams} onChange={refresh} />
      )}
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
      {tab === "notifications" && <NotificationsPanel />}
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
        searchable: true,
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
        searchable: true,
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
        searchable: true,
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
        <>
          <div className="flex justify-end">
            <Button onClick={() => setOpen(true)}>
              <PlusCircle className="h-4 w-4" /> New team
            </Button>
          </div>
          <EmptyState
            title="No teams yet"
            description="Create your first team to start grouping services and rosters."
            learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/paging-guide.md"
            learnMoreLabel="Paging guide"
          />
        </>
      ) : (
        <DataTable
          rows={teams}
          columns={teamColumns}
          rowKey={(t) => t.id}
          storageKey="opsmender:teams-table"
          searchPlaceholder="Search by name, slug, or description…"
          dateRangeColumn={{
            id: "created_at",
            label: "Created",
            valueOf: (t) => t.created_at,
          }}
          toolbarRight={
            <Button onClick={() => setOpen(true)}>
              <PlusCircle className="h-4 w-4" /> New team
            </Button>
          }
          rowActions={(t) => (
            <Button variant="ghost" onClick={() => remove(t.id)} title="Delete">
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        />
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
              onChange={(e) =>
                setForm({ ...form, slug: normalizeSlugInput(e.target.value) })
              }
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

interface ServiceRow {
  service: ServiceResponse;
  team_name: string | null;
  on_call_user_id: string | null;
  on_call_username: string | null;
  open_incidents: number;
  last_incident_at: string | null;
}

type ServiceStatusFilter = "" | "active" | "inactive";
type ServiceCoverageFilter = "" | "covered" | "uncovered";
type ServiceIncidentFilter = "" | "has_open" | "no_open";
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
  onChange,
}: {
  services: ServiceResponse[];
  teams: TeamResponse[];
  rosters: RosterResponse[];
  onChange: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ServiceResponse | null>(null);
  const [form, setForm] = useState({
    name: "",
    slug: "",
    team_id: "",
    description: "",
    priority: "P2" as Priority,
    preferred_mcp_server_ids: [] as string[],
  });
  const [serviceSearch, setServiceSearch] = useState("");
  const [teamFilter, setTeamFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<ServiceStatusFilter>("");
  const [coverageFilter, setCoverageFilter] = useState<ServiceCoverageFilter>("");
  const [incidentFilter, setIncidentFilter] = useState<ServiceIncidentFilter>("");
  const [timeFilter, setTimeFilter] = useState<ServiceTimeFilter>("");
  const [incidents, setIncidents] = useState<IncidentResponse[]>([]);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServerResponse[]>([]);
  const [onCallByTeam, setOnCallByTeam] = useState<Map<string, string | null>>(
    new Map(),
  );
  const deferredServiceSearch = useDeferredValue(serviceSearch);

  useEffect(() => {
    if (teams.length > 0 && !form.team_id) {
      setForm((f) => ({ ...f, team_id: teams[0].id }));
    }
  }, [teams, form.team_id]);

  // Enrich rows: load incidents + users + per-team on-call resolution.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [incList, uList, mcpList] = await Promise.all([
          listIncidents({ limit: 200 }).catch(() => ({
            items: [] as IncidentResponse[],
            total: 0,
          })),
          listUsers().catch(() => ({ items: [] as UserResponse[], total: 0 })),
          listMCPServers().catch(() => ({ items: [] as MCPServerResponse[], total: 0 })),
        ]);
        if (cancelled) return;
        setIncidents(incList.items);
        setUsers(uList.items);
        setMcpServers(mcpList.items);

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
      };
      if (editing) {
        await updateService(editing.id, payload);
      } else {
        await createService({
          ...payload,
          slug: form.slug,
        });
      }
      setOpen(false);
      setEditing(null);
      setForm({
        name: "",
        slug: "",
        team_id: teams[0]?.id ?? "",
        description: "",
        priority: "P2",
        preferred_mcp_server_ids: [],
      });
      onChange();
      toast.success(editing ? "Service updated" : "Service created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const openCreate = () => {
    setEditing(null);
    setForm({
      name: "",
      slug: "",
      team_id: teams[0]?.id ?? "",
      description: "",
      priority: "P2",
      preferred_mcp_server_ids: [],
    });
    setOpen(true);
  };

  const openEdit = (service: ServiceResponse) => {
    setEditing(service);
    setForm({
      name: service.name,
      slug: service.slug,
      team_id: service.team_id,
      description: service.description ?? "",
      priority: service.priority,
      preferred_mcp_server_ids: service.preferred_mcp_server_ids ?? [],
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
      if (teamFilter && row.service.team_id !== teamFilter) return false;
      if (statusFilter) {
        const status = row.service.is_active ? "active" : "inactive";
        if (status !== statusFilter) return false;
      }
      if (coverageFilter === "covered" && !row.on_call_username) return false;
      if (coverageFilter === "uncovered" && row.on_call_username) return false;
      if (incidentFilter === "has_open" && row.open_incidents <= 0) return false;
      if (incidentFilter === "no_open" && row.open_incidents > 0) return false;
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
      teamFilter ||
      statusFilter ||
      coverageFilter ||
      incidentFilter ||
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
      cell: (r) => (
        <span className="font-mono text-[11px] text-fg-secondary">
          {r.service.intake_url ?? "not generated"}
        </span>
      ),
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
            teams.length > 0 ? (
              <Button onClick={openCreate}>
                <PlusCircle className="h-4 w-4" /> New service
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <ServiceFilterBar
            search={serviceSearch}
            onSearchChange={setServiceSearch}
            teams={teams}
            teamFilter={teamFilter}
            onTeamFilterChange={setTeamFilter}
            statusFilter={statusFilter}
            onStatusFilterChange={setStatusFilter}
            coverageFilter={coverageFilter}
            onCoverageFilterChange={setCoverageFilter}
            incidentFilter={incidentFilter}
            onIncidentFilterChange={setIncidentFilter}
            timeFilter={timeFilter}
            onTimeFilterChange={setTimeFilter}
            hasFilters={hasServiceFilters}
            onClear={() => {
              setServiceSearch("");
              setTeamFilter("");
              setStatusFilter("");
              setCoverageFilter("");
              setIncidentFilter("");
              setTimeFilter("");
            }}
            action={
              <Button
                size="sm"
                onClick={openCreate}
                disabled={teams.length === 0}
              >
                <PlusCircle className="h-4 w-4" /> New service
              </Button>
            }
          />
          <DataTable
            rows={filteredRows}
            columns={columns}
            rowKey={(r) => r.service.id}
            storageKey="opsmender:services-table"
            hideToolbar
            rowActions={(r) => (
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
            <Label>Preferred MCP servers</Label>
            <select
              multiple
              value={form.preferred_mcp_server_ids}
              onChange={(e) =>
                setForm({
                  ...form,
                  preferred_mcp_server_ids: Array.from(
                    e.currentTarget.selectedOptions,
                    (option) => option.value,
                  ),
                })
              }
              className="min-h-28 w-full rounded-md border border-border-subtle bg-bg-input px-3 py-2 text-sm text-fg-primary outline-none focus:border-accent"
            >
              {mcpServers.map((server) => (
                <option key={server.id} value={server.id}>
                  {server.name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-fg-muted">
              Ordered preference list. OpsMender tries these first to reduce
              tool noise; operators can still ask for another configured MCP
              server manually.
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

function ServiceFilterBar({
  search,
  onSearchChange,
  teams,
  teamFilter,
  onTeamFilterChange,
  statusFilter,
  onStatusFilterChange,
  coverageFilter,
  onCoverageFilterChange,
  incidentFilter,
  onIncidentFilterChange,
  timeFilter,
  onTimeFilterChange,
  hasFilters,
  onClear,
  action,
}: {
  search: string;
  onSearchChange: (value: string) => void;
  teams: TeamResponse[];
  teamFilter: string;
  onTeamFilterChange: (value: string) => void;
  statusFilter: ServiceStatusFilter;
  onStatusFilterChange: (value: ServiceStatusFilter) => void;
  coverageFilter: ServiceCoverageFilter;
  onCoverageFilterChange: (value: ServiceCoverageFilter) => void;
  incidentFilter: ServiceIncidentFilter;
  onIncidentFilterChange: (value: ServiceIncidentFilter) => void;
  timeFilter: ServiceTimeFilter;
  onTimeFilterChange: (value: ServiceTimeFilter) => void;
  hasFilters: boolean;
  onClear: () => void;
  action: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-3 shadow-sm">
      <div className="grid gap-3 xl:grid-cols-[minmax(16rem,1.25fr)_repeat(5,minmax(8rem,0.7fr))_auto]">
        <div className="relative">
          <Search
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
          />
          <Input
            aria-label="Search services"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search services..."
            className="h-11 pl-9"
          />
        </div>
        <Select
          aria-label="Filter services by team"
          value={teamFilter}
          onChange={(e) => onTeamFilterChange(e.target.value)}
          className="h-11"
        >
          <option value="">All teams</option>
          {teams.map((team) => (
            <option key={team.id} value={team.id}>
              {team.name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter services by status"
          value={statusFilter}
          onChange={(e) => onStatusFilterChange(e.target.value as ServiceStatusFilter)}
          className="h-11"
        >
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </Select>
        <Select
          aria-label="Filter services by on-call coverage"
          value={coverageFilter}
          onChange={(e) =>
            onCoverageFilterChange(e.target.value as ServiceCoverageFilter)
          }
          className="h-11"
        >
          <option value="">All coverage</option>
          <option value="covered">On-call covered</option>
          <option value="uncovered">No on-call</option>
        </Select>
        <Select
          aria-label="Filter services by open incidents"
          value={incidentFilter}
          onChange={(e) =>
            onIncidentFilterChange(e.target.value as ServiceIncidentFilter)
          }
          className="h-11"
        >
          <option value="">All incident states</option>
          <option value="has_open">Has open incidents</option>
          <option value="no_open">No open incidents</option>
        </Select>
        <Select
          aria-label="Filter services by last incident"
          value={timeFilter}
          onChange={(e) => onTimeFilterChange(e.target.value as ServiceTimeFilter)}
          className="h-11"
        >
          {SERVICE_TIME_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
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
  const [calendarFor, setCalendarFor] = useState<RosterResponse | null>(null);
  const [form, setForm] = useState({
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
        description: form.description || undefined,
        time_zone: form.time_zone,
        pattern: form.pattern,
        pattern_length: form.pattern_length,
        coverage_start_time: form.coverage_start_time,
        coverage_end_time: form.coverage_end_time,
        handoff_time: form.coverage_start_time,
        anchor_date: form.anchor_date,
        is_active: form.is_active,
      });
      setOpen(false);
      onChange();
      toast.success("Roster created");
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
    () =>
      Array.from(
        new Map(
          rosters
            .map((r) => teamNameById.get(r.team_id))
            .filter((n): n is string => Boolean(n))
            .map((name) => [name, { value: name, label: name }]),
        ).values(),
      ),
    [rosters, teamNameById],
  );

  const rosterColumns = useMemo<DataTableColumn<RosterResponse>[]>(
    () => [
      {
        id: "name",
        label: "Roster",
        accessor: (r) => r.name,
        sortable: true,
        searchable: true,
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
        searchable: true,
        filterChips: {
          options: teamFilterOptions,
          valueOf: (r) => teamNameById.get(r.team_id) ?? null,
        },
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
        filterChips: {
          options: [
            { value: "weekly", label: "Weekly" },
            { value: "daily", label: "Daily" },
            { value: "custom_n_days", label: "Custom" },
          ],
          valueOf: (r) => r.pattern,
        },
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
    [teamNameById, teamFilterOptions],
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
            <Button onClick={() => setOpen(true)} disabled={teams.length === 0}>
              <PlusCircle className="h-4 w-4" /> New roster
            </Button>
          }
        />
      ) : (
        <DataTable
          rows={rosters}
          columns={rosterColumns}
          rowKey={(r) => r.id}
          storageKey="opsmender:rosters-table"
          searchPlaceholder="Search by roster name or team…"
          toolbarRight={
            <>
              <span className="text-sm text-fg-secondary">
                {rosters.length} roster{rosters.length === 1 ? "" : "s"}
              </span>
              <Button onClick={() => setOpen(true)} disabled={teams.length === 0}>
                <PlusCircle className="h-4 w-4" /> New roster
              </Button>
            </>
          }
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
              <Button variant="ghost" onClick={() => remove(r.id)} title="Delete">
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          )}
        />
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="Create roster schedule">
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
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit}>Create</Button>
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
    () =>
      Array.from(
        new Map(
          chains
            .map((c) => teamNameById.get(c.team_id))
            .filter((n): n is string => Boolean(n))
            .map((name) => [name, { value: name, label: name }]),
        ).values(),
      ),
    [chains, teamNameById],
  );

  const chainColumns = useMemo<DataTableColumn<EscalationChainResponse>[]>(
    () => [
      {
        id: "name",
        label: "Chain",
        accessor: (c) => c.name,
        sortable: true,
        searchable: true,
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
        searchable: true,
        filterChips: {
          options: teamFilterOptions,
          valueOf: (c) => teamNameById.get(c.team_id) ?? null,
        },
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
        searchable: true,
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
        filterChips: {
          options: [
            { value: "active", label: "Active" },
            { value: "inactive", label: "Inactive" },
          ],
          valueOf: (c) => (c.is_active ? "active" : "inactive"),
        },
        cell: (c) =>
          c.is_active ? (
            <Badge variant="resolved">active</Badge>
          ) : (
            <Badge variant="closed">inactive</Badge>
          ),
      },
    ],
    [teamNameById, teamFilterOptions],
  );

  return (
    <section className="space-y-3">
      {chains.length === 0 ? (
        <EmptyState
          title="No escalation chains"
          description="Without a chain, incidents flagged for paging do not page anyone. Create one per team."
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/paging-guide.md"
          learnMoreLabel="Paging guide"
          action={
            <Button onClick={() => setOpen(true)} disabled={teams.length === 0}>
              <PlusCircle className="h-4 w-4" /> New chain
            </Button>
          }
        />
      ) : (
        <DataTable
          rows={chains}
          columns={chainColumns}
          rowKey={(c) => c.id}
          storageKey="opsmender:chains-table"
          searchPlaceholder="Search by chain name, team, or description…"
          toolbarRight={
            <>
              <span className="text-sm text-fg-secondary">
                {chains.length} chain{chains.length === 1 ? "" : "s"}
              </span>
              <Button onClick={() => setOpen(true)} disabled={teams.length === 0}>
                <PlusCircle className="h-4 w-4" /> New chain
              </Button>
            </>
          }
          empty={
            <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
              No chains match the current filters.
            </div>
          }
          expandedRow={{
            expandedKeys,
            onToggle: (key) => void toggleExpand(key),
            label: "Steps",
            render: (c) => (
              <StepsEditor
                chainId={c.id}
                steps={stepsByChain[c.id] ?? []}
                rosters={rosters}
                teams={teams}
                onChange={() => loadStepsFor(c.id)}
              />
            ),
          }}
          rowActions={(c) => (
            <Button
              variant="ghost"
              onClick={() => remove(c.id)}
              title="Delete chain"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        />
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

  const targetOptions =
    stepForm.target_type === "roster"
      ? rosters
      : stepForm.target_type === "team"
        ? teams
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
                const targetLabel =
                  s.target_type === "roster"
                    ? rosters.find((r) => r.id === s.target_id)?.name
                    : s.target_type === "team"
                      ? teams.find((t) => t.id === s.target_id)?.name
                      : s.target_id;
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
                      Page <span className="font-medium text-fg-primary">{targetLabel}</span>{" "}
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
                  <Badge variant="default">#{s.step_index}</Badge>
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
    scope_ids: string[];
  }>({
    name: "",
    description: "",
    reason: "",
    starts_at: nowMinusOne(),
    ends_at: nowPlusTwo(),
    scope_type: "global",
    scope_ids: [],
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
    const ids = w.scope_ids?.length ? w.scope_ids : w.scope_id ? [w.scope_id] : [];
    const names = ids.map((id) => opts.find((o) => o.id === id)?.name ?? id);
    return `${w.scope_type}: ${names.join(", ") || "?"}`;
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
    try {
      await createMaintenanceWindow({
        name: form.name.trim(),
        description: form.description.trim() || null,
        reason: form.reason.trim() || null,
        starts_at: new Date(form.starts_at).toISOString(),
        ends_at: new Date(form.ends_at).toISOString(),
        scope_type: form.scope_type,
        scope_id: form.scope_type === "global" ? null : form.scope_ids[0],
        scope_ids: form.scope_type === "global" ? [] : form.scope_ids,
      });
      setOpen(false);
      setForm({
        name: "",
        description: "",
        reason: "",
        starts_at: nowMinusOne(),
        ends_at: nowPlusTwo(),
        scope_type: "global",
        scope_ids: [],
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
              ? "Schedule a window to drop matching alerts during planned work before they create visible incidents."
              : view === "active"
                ? "No windows are currently in effect."
                : "No past windows match the current range filter."
          }
          learnMoreHref={
            view === "scheduled" || view === "active"
              ? "https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/paging-guide.md"
              : "https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/notification-preferences.md"
          }
          learnMoreLabel={
            view === "scheduled" || view === "active"
              ? "Paging guide"
              : "Notification guide"
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
                    scope_ids: [],
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
                <Label>Targets</Label>
                <select
                  multiple
                  value={form.scope_ids}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      scope_ids: Array.from(
                        e.currentTarget.selectedOptions,
                        (option) => option.value,
                      ),
                    })
                  }
                  className="min-h-28 w-full rounded-md border border-border-subtle bg-bg-input px-3 py-2 text-sm text-fg-primary outline-none focus:border-accent"
                >
                  {scopeOptionsFor(form.scope_type).map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>
          <p className="text-xs text-fg-tertiary">
            Matching alerts are dropped during the window so planned work does
            not create noisy visible incidents. Non-matching alerts still flow.
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

function NotificationsPanel() {
  return (
    <section className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-4">
          <h3 className="text-sm font-semibold text-fg-primary">
            Operator Delivery
          </h3>
          <p className="mt-1 text-sm text-fg-secondary">
            Configure the workspace-level channels used to page admins and
            operators. Chat-capable adapters can also host incident sessions.
          </p>
          <div className="mt-4">
            <NotificationChannelsPage embedded />
          </div>
        </div>
        <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-4">
          <h3 className="text-sm font-semibold text-fg-primary">
            Viewer Updates
          </h3>
          <p className="mt-1 text-sm text-fg-secondary">
            Send read-only status updates to viewers and downstream workflows
            without making them on-call operators.
          </p>
          <div className="mt-4">
            <OutboundHooksPage embedded />
          </div>
        </div>
      </div>
      <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-4">
        <h3 className="text-sm font-semibold text-fg-primary">Quiet Hours</h3>
        <p className="mt-1 text-sm text-fg-secondary">
          Personal quiet-hours settings apply where appropriate while urgent
          escalation behavior can still break through by priority.
        </p>
      </div>
      <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-4">
        <h3 className="text-sm font-semibold text-fg-primary">
          Routing by Priority
        </h3>
        <p className="mt-1 text-sm text-fg-secondary">
          Choose which configured channels receive P0, P1, P2, and P3
          notifications. The options come from configured delivery channels.
        </p>
      </div>
      <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-4">
        <h3 className="text-sm font-semibold text-fg-primary">
          Sessions / Chat
        </h3>
        <p className="mt-1 text-sm text-fg-secondary">
          Incident sessions are only available on chat-capable notification
          adapters. Email, SMS, and generic webhooks remain delivery-only.
        </p>
      </div>
      <NotificationPreferencesPanel />
    </section>
  );
}

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
