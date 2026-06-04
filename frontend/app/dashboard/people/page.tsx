"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Copy,
  FileSpreadsheet,
  Mail,
  PlusCircle,
  RotateCcw,
  Trash2,
  UserPlus,
  Users,
} from "lucide-react";

import {
  createInvite,
  createUser,
  getConfig,
  listInvites,
  listUsers,
  resendInvite,
  revokeInvite,
} from "@/lib/api";
import type {
  InviteCreateRequest,
  InviteCreatedResponse,
  InviteResponse,
  InviteStatus,
  UserCreateRequest,
  UserResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { FormError, Input, Label, Select } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";


type Tab = "users" | "invites";
type MintedInviteMode = "created" | "resent";
type ParsedBulkInviteLine = InviteCreateRequest & { lineNumber: number };
type BulkInviteFailure = {
  lineNumber: number;
  raw: string;
  error: string;
};
type BulkInviteResult = {
  successes: InviteCreatedResponse[];
  failures: BulkInviteFailure[];
};


const ROLE_VARIANT: Record<UserResponse["role"], string> = {
  admin: "high",
  operator: "medium",
  viewer: "default",
};

const INVITE_STATUS_VARIANT: Record<InviteStatus, string> = {
  pending: "medium",
  accepted: "low",
  expired: "default",
  revoked: "high",
};


function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function parseBulkInviteLines(input: string): {
  entries: ParsedBulkInviteLine[];
  failures: BulkInviteFailure[];
} {
  const entries: ParsedBulkInviteLine[] = [];
  const failures: BulkInviteFailure[] = [];
  const allowedRoles = new Set(["admin", "operator", "viewer"]);

  input.split(/\r?\n/).forEach((rawLine, index) => {
    const lineNumber = index + 1;
    const line = rawLine.trim();
    if (!line) return;

    const parts = line.split(",").map((part) => part.trim()).filter(Boolean);
    if (parts.length !== 2) {
      failures.push({
        lineNumber,
        raw: rawLine,
        error: "Use exactly: email, role",
      });
      return;
    }

    const [email, roleRaw] = parts;
    const role = roleRaw.toLowerCase();
    const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    if (!emailOk) {
      failures.push({
        lineNumber,
        raw: rawLine,
        error: "Invalid email address",
      });
      return;
    }
    if (!allowedRoles.has(role)) {
      failures.push({
        lineNumber,
        raw: rawLine,
        error: "Role must be admin, operator, or viewer",
      });
      return;
    }

    entries.push({
      lineNumber,
      email,
      role: role as InviteCreateRequest["role"],
    });
  });

  return { entries, failures };
}

function authMethodMeta(user: UserResponse) {
  const value = user.auth_source || "local";
  if (value.startsWith("oidc:")) {
    const slug = value.slice("oidc:".length) || "org";
    return {
      label: `oidc:${slug}`,
      variant: "medium" as const,
      href: user.primary_org_id
        ? `/dashboard/organizations?org=${user.primary_org_id}&auth=oidc`
        : "/dashboard/organizations",
    };
  }
  if (value.startsWith("saml:")) {
    const slug = value.slice("saml:".length) || "org";
    return {
      label: `saml:${slug}`,
      variant: "default" as const,
      href: user.primary_org_id
        ? `/dashboard/organizations?org=${user.primary_org_id}&auth=saml`
        : "/dashboard/organizations",
    };
  }
  return { label: "local", variant: "low" as const, href: null };
}

function AuthMethodBadge({ user }: { user: UserResponse }) {
  const meta = authMethodMeta(user);
  const badge = <Badge variant={meta.variant as never}>{meta.label}</Badge>;
  if (!meta.href) return badge;
  return (
    <Link href={meta.href} className="hover:opacity-80">
      {badge}
    </Link>
  );
}


export default function PeoplePage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  if (!isAdmin) {
    return (
      <div className="mx-auto max-w-3xl py-12">
        <EmptyState
          icon={Users}
          title="Admin only"
          description="The People page is restricted to administrators."
        />
      </div>
    );
  }

  return <PeopleSurface />;
}


function PeopleSurface() {
  const { user } = useAuth();
  const orgId = user?.primary_org_id ?? null;
  const toast = useToast();

  const [tab, setTab] = useState<Tab>("users");
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [invites, setInvites] = useState<InviteResponse[]>([]);
  const [usersLoading, setUsersLoading] = useState(true);
  const [invitesLoading, setInvitesLoading] = useState(true);
  // v1 is local-auth only by default. Surface SSO/SAML affordances (the auth
  // method column + filter) only when advanced auth is enabled or already
  // configured — matching the D-027 rule used elsewhere.
  const [advancedAuth, setAdvancedAuth] = useState(false);
  const [newUserOpen, setNewUserOpen] = useState(false);
  const [newInviteOpen, setNewInviteOpen] = useState(false);
  const [bulkInviteOpen, setBulkInviteOpen] = useState(false);
  const [mintedInvite, setMintedInvite] = useState<{
    mode: MintedInviteMode;
    payload: InviteCreatedResponse;
  } | null>(null);
  const [bulkInviteResult, setBulkInviteResult] = useState<BulkInviteResult | null>(
    null,
  );

  const reloadUsers = useCallback(async () => {
    setUsersLoading(true);
    try {
      const res = await listUsers({ limit: 500 });
      setUsers(res.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setUsersLoading(false);
    }
  }, [toast]);

  const reloadInvites = useCallback(async () => {
    if (!orgId) {
      setInvitesLoading(false);
      return;
    }
    setInvitesLoading(true);
    try {
      const res = await listInvites(orgId);
      setInvites(res.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setInvitesLoading(false);
    }
  }, [orgId, toast]);

  useEffect(() => {
    void reloadUsers();
  }, [reloadUsers]);

  useEffect(() => {
    void reloadInvites();
  }, [reloadInvites]);

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await getConfig();
        setAdvancedAuth(
          Boolean(
            cfg.advanced_auth_enabled || cfg.sso_configured || cfg.saml_configured,
          ),
        );
      } catch {
        setAdvancedAuth(false);
      }
    })();
  }, []);

  const onInviteCreated = useCallback(
    (resp: InviteCreatedResponse, mode: MintedInviteMode = "created") => {
      setMintedInvite({ mode, payload: resp });
      setNewInviteOpen(false);
      void reloadInvites();
    },
    [reloadInvites],
  );

  const onBulkInviteCompleted = useCallback(
    (result: BulkInviteResult) => {
      setBulkInviteResult(result);
      setBulkInviteOpen(false);
      void reloadInvites();
    },
    [reloadInvites],
  );

  const onRevoke = useCallback(
    async (invite: InviteResponse) => {
      if (!orgId) return;
      if (!confirm(`Revoke invite for ${invite.email}?`)) return;
      try {
        await revokeInvite(orgId, invite.id);
        toast.success("Invite revoked");
        void reloadInvites();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    },
    [orgId, reloadInvites, toast],
  );

  const onResend = useCallback(
    async (invite: InviteResponse) => {
      if (!orgId) return;
      if (!confirm(`Resend invite for ${invite.email}? The old link will stop working.`)) {
        return;
      }
      try {
        const resp = await resendInvite(orgId, invite.id);
        toast.success(
          resp.email_sent
            ? "Invite resent — new link also available below"
            : "Invite reissued — copy the new link below",
        );
        onInviteCreated(resp, "resent");
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    },
    [onInviteCreated, orgId, toast],
  );

  return (
    <div className="w-full">
      <PageHeader
        title="People"
        subtitle="Manage user accounts and pending invites."
      />

      <nav
        aria-label="People sections"
        className="mb-5 flex flex-wrap gap-2"
      >
        {(
          [
            { id: "users", label: "Users", icon: Users, count: users.length },
            {
              id: "invites",
              label: "Invites",
              icon: Mail,
              count: invites.filter((i) => i.status === "pending").length,
            },
          ] as const
        ).map(({ id, label, icon: Icon, count }) => {
          const active = tab === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              aria-current={active ? "page" : undefined}
              className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition ${
                active
                  ? "bg-accent text-white shadow-sm"
                  : "border border-border-default bg-bg-surface text-fg-secondary hover:border-border-strong hover:text-fg-primary"
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
              <span
                className={`ml-1 rounded-pill px-1.5 text-[10px] font-semibold ${
                  active ? "bg-white/20" : "bg-bg-elevated text-fg-muted"
                }`}
              >
                {count}
              </span>
            </button>
          );
        })}
      </nav>

      {tab === "users" ? (
        <UsersTab
          users={users}
          loading={usersLoading}
          advancedAuth={advancedAuth}
          onNewUser={() => setNewUserOpen(true)}
        />
      ) : (
        <InvitesTab
          invites={invites}
          loading={invitesLoading}
          onNew={() => setNewInviteOpen(true)}
          onBulk={() => setBulkInviteOpen(true)}
          onRevoke={onRevoke}
          onResend={onResend}
        />
      )}

      <CreateUserModal
        open={newUserOpen}
        onClose={() => setNewUserOpen(false)}
        onCreated={() => {
          setNewUserOpen(false);
          void reloadUsers();
        }}
      />

      <NewInviteModal
        open={newInviteOpen}
        onClose={() => setNewInviteOpen(false)}
        orgId={orgId}
        onCreated={onInviteCreated}
      />
      <BulkInviteModal
        open={bulkInviteOpen}
        onClose={() => setBulkInviteOpen(false)}
        orgId={orgId}
        onCompleted={onBulkInviteCompleted}
      />

      <MintedInviteModal
        invite={mintedInvite}
        onClose={() => setMintedInvite(null)}
      />
      <BulkInviteResultModal
        result={bulkInviteResult}
        onClose={() => setBulkInviteResult(null)}
      />
    </div>
  );
}


// ---------------------------------------------------------------------------
// Users tab
// ---------------------------------------------------------------------------


function UsersTab({
  users,
  loading,
  advancedAuth,
  onNewUser,
}: {
  users: UserResponse[];
  loading: boolean;
  advancedAuth: boolean;
  onNewUser: () => void;
}) {
  const columns = useMemo<DataTableColumn<UserResponse>[]>(
    () => [
      {
        id: "username",
        label: "User",
        accessor: (u) => u.username,
        cell: (u) => (
          <div>
            <Link
              href={`/dashboard/people/detail?id=${u.id}`}
              className="font-medium text-fg-primary hover:text-accent"
            >
              {u.username}
            </Link>
            <p className="text-xs text-fg-muted">{u.email}</p>
          </div>
        ),
        sortable: true,
        searchable: true,
      },
      {
        id: "role",
        label: "Role",
        accessor: (u) => u.role,
        cell: (u) => (
          <Badge variant={ROLE_VARIANT[u.role] as never}>{u.role}</Badge>
        ),
        sortable: true,
        filterChips: {
          options: [
            { value: "admin", label: "Admin" },
            { value: "operator", label: "Operator" },
            { value: "viewer", label: "Viewer" },
          ],
          valueOf: (u) => u.role,
        },
      },
      {
        id: "status",
        label: "Status",
        accessor: (u) => (u.is_active ? "active" : "inactive"),
        cell: (u) =>
          u.is_active ? (
            <Badge variant="low">Active</Badge>
          ) : (
            <Badge variant="default">Inactive</Badge>
          ),
        sortable: true,
        filterChips: {
          options: [
            { value: "active", label: "Active" },
            { value: "inactive", label: "Inactive" },
          ],
          valueOf: (u) => (u.is_active ? "active" : "inactive"),
        },
      },
      // Auth-method column + filter only when advanced auth is in play; v1
      // default is local-only, so we don't surface OIDC/SAML affordances.
      ...(advancedAuth
        ? [
            {
              id: "auth_source",
              label: "Auth method",
              accessor: (u: UserResponse) => {
                if (u.auth_source.startsWith("oidc:")) return "oidc";
                if (u.auth_source.startsWith("saml:")) return "saml";
                return "local";
              },
              cell: (u: UserResponse) => <AuthMethodBadge user={u} />,
              sortable: true,
              filterChips: {
                options: [
                  { value: "local", label: "Local" },
                  { value: "oidc", label: "OIDC" },
                  { value: "saml", label: "SAML" },
                ],
                valueOf: (u: UserResponse) => {
                  if (u.auth_source.startsWith("oidc:")) return "oidc";
                  if (u.auth_source.startsWith("saml:")) return "saml";
                  return "local";
                },
              },
            } as DataTableColumn<UserResponse>,
          ]
        : []),
      {
        id: "created_at",
        label: "Joined",
        accessor: (u) => u.created_at,
        cell: (u) => (
          <span className="whitespace-nowrap text-sm text-fg-secondary">
            {fmtDate(u.created_at)}
          </span>
        ),
        sortable: true,
      },
    ],
    [advancedAuth],
  );

  if (loading && users.length === 0) return <TableSkeleton rows={6} columns={5} />;
  if (users.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No users yet"
        description="Create a user directly, or invite a teammate from the Invites tab."
        action={
          <Button onClick={onNewUser}>
            <UserPlus className="h-4 w-4" /> New user
          </Button>
        }
      />
    );
  }

  return (
    <DataTable
      rows={users}
      columns={columns}
      filterBar
      toolbarRight={
        <Button onClick={onNewUser}>
          <UserPlus className="h-4 w-4" /> New user
        </Button>
      }
      rowKey={(u) => u.id}
      phoneLayout={(u) => (
        <div className="space-y-3">
          <div className="min-w-0">
            <Link
              href={`/dashboard/people/detail?id=${u.id}`}
              className="font-medium text-fg-primary hover:text-accent"
            >
              {u.username}
            </Link>
            <p className="mt-1 text-sm text-fg-muted">{u.email}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={ROLE_VARIANT[u.role] as never}>{u.role}</Badge>
            {u.is_active ? (
              <Badge variant="low">Active</Badge>
            ) : (
              <Badge variant="default">Inactive</Badge>
            )}
          </div>
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            {advancedAuth && (
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
                  Auth method
                </p>
                <div className="mt-1">
                  <AuthMethodBadge user={u} />
                </div>
              </div>
            )}
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
                Joined
              </p>
              <p className="mt-1 text-fg-secondary">{fmtDate(u.created_at)}</p>
            </div>
          </div>
        </div>
      )}
      storageKey="opsmender:people-users-table"
      searchPlaceholder="Search by username or email…"
      dateRangeColumn={{
        id: "created_at",
        label: "Joined",
        valueOf: (u) => u.created_at,
      }}
      rowActions={(u) => (
        <Link
          href={`/dashboard/people/detail?id=${u.id}`}
          className="text-sm font-medium text-accent hover:underline"
        >
          Manage
        </Link>
      )}
    />
  );
}


// ---------------------------------------------------------------------------
// Invites tab
// ---------------------------------------------------------------------------


function InvitesTab({
  invites,
  loading,
  onNew,
  onBulk,
  onRevoke,
  onResend,
}: {
  invites: InviteResponse[];
  loading: boolean;
  onNew: () => void;
  onBulk: () => void;
  onRevoke: (invite: InviteResponse) => void;
  onResend: (invite: InviteResponse) => void;
}) {
  const columns = useMemo<DataTableColumn<InviteResponse>[]>(
    () => [
      {
        id: "email",
        label: "Email",
        accessor: (i) => i.email,
        cell: (i) => (
          <span className="font-medium text-fg-primary">{i.email}</span>
        ),
        sortable: true,
        searchable: true,
      },
      {
        id: "role",
        label: "Role",
        accessor: (i) => i.role,
        cell: (i) => (
          <Badge variant={ROLE_VARIANT[i.role] as never}>{i.role}</Badge>
        ),
        sortable: true,
        filterChips: {
          options: [
            { value: "admin", label: "Admin" },
            { value: "operator", label: "Operator" },
            { value: "viewer", label: "Viewer" },
          ],
          valueOf: (i) => i.role,
        },
      },
      {
        id: "status",
        label: "Status",
        accessor: (i) => i.status,
        cell: (i) => (
          <Badge variant={INVITE_STATUS_VARIANT[i.status] as never}>
            {i.status}
          </Badge>
        ),
        sortable: true,
        filterChips: {
          options: [
            { value: "pending", label: "Pending" },
            { value: "accepted", label: "Accepted" },
            { value: "expired", label: "Expired" },
            { value: "revoked", label: "Revoked" },
          ],
          valueOf: (i) => i.status,
        },
      },
      {
        id: "expires_at",
        label: "Expires",
        accessor: (i) => i.expires_at,
        cell: (i) => (
          <span className="whitespace-nowrap text-sm text-fg-secondary">
            {fmtDate(i.expires_at)}
          </span>
        ),
        sortable: true,
      },
      {
        id: "created_at",
        label: "Sent",
        accessor: (i) => i.created_at,
        cell: (i) => (
          <span className="whitespace-nowrap text-sm text-fg-muted">
            {fmtDate(i.created_at)}
          </span>
        ),
        sortable: true,
      },
    ],
    [],
  );

  if (loading && invites.length === 0) {
    return <TableSkeleton rows={4} columns={5} />;
  }
  if (invites.length === 0) {
    return (
      <div className="space-y-4">
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onBulk}>
            <FileSpreadsheet className="h-4 w-4" /> Bulk import
          </Button>
          <Button onClick={onNew}>
            <UserPlus className="h-4 w-4" /> New invite
          </Button>
        </div>
        <EmptyState
          icon={Mail}
          title="No invites yet"
          description="Click ‘New invite’ to send someone a one-time signup link."
        />
      </div>
    );
  }

  return (
    <DataTable
      rows={invites}
      columns={columns}
      filterBar
      rowKey={(i) => i.id}
      phoneLayout={(invite) => (
        <div className="space-y-3">
          <div className="min-w-0">
            <p className="font-medium text-fg-primary">{invite.email}</p>
            <p className="mt-1 text-sm text-fg-muted">
              Sent {fmtDate(invite.created_at)}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={ROLE_VARIANT[invite.role] as never}>{invite.role}</Badge>
            <Badge variant={INVITE_STATUS_VARIANT[invite.status] as never}>
              {invite.status}
            </Badge>
          </div>
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
                Expires
              </p>
              <p className="mt-1 text-fg-secondary">{fmtDate(invite.expires_at)}</p>
            </div>
          </div>
        </div>
      )}
      storageKey="opsmender:people-invites-table"
      searchPlaceholder="Search by email…"
      dateRangeColumn={{
        id: "created_at",
        label: "Sent",
        valueOf: (i) => i.created_at,
      }}
      toolbarRight={
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" onClick={onBulk}>
            <FileSpreadsheet className="h-4 w-4" /> Bulk import
          </Button>
          <Button onClick={onNew}>
            <UserPlus className="h-4 w-4" /> New invite
          </Button>
        </div>
      }
      rowActions={(invite) =>
        invite.status === "pending" ? (
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onResend(invite)}
              title="Resend"
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onRevoke(invite)}
              title="Revoke"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ) : null
      }
    />
  );
}


// ---------------------------------------------------------------------------
// Create user modal (direct admin creation — no invite link required)
// ---------------------------------------------------------------------------


function randomTempPassword(): string {
  // 16 url-safe chars — enough entropy for a temporary password the admin
  // hands off and the user rotates on first login.
  const bytes = new Uint8Array(12);
  (globalThis.crypto ?? window.crypto).getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes)).replace(/[+/=]/g, "").slice(0, 16);
}


function CreateUserModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<UserCreateRequest>({
    username: "",
    email: "",
    role: "operator",
    password: "",
    is_active: true,
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setForm({
        username: "",
        email: "",
        role: "operator",
        password: randomTempPassword(),
        is_active: true,
      });
      setError("");
    }
  }, [open]);

  const submit = useCallback(async () => {
    if (!form.username.trim() || !form.email.trim()) {
      setError("Username and email are required.");
      return;
    }
    if ((form.password ?? "").length < 8) {
      setError("Temporary password must be at least 8 characters.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await createUser({
        username: form.username.trim(),
        email: form.email.trim(),
        role: form.role,
        password: form.password,
        is_active: form.is_active,
      });
      toast.success(`User “${form.username.trim()}” created`);
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [form, onCreated, toast]);

  const copyPassword = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(form.password);
      toast.success("Temporary password copied");
    } catch {
      toast.error("Copy failed — select and copy manually.");
    }
  }, [form.password, toast]);

  return (
    <Modal open={open} onClose={onClose} title="New user">
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <Label>Username</Label>
            <Input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              placeholder="jdoe"
              autoFocus
            />
          </div>
          <div>
            <Label>Email</Label>
            <Input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              placeholder="jdoe@company.com"
            />
          </div>
        </div>
        <div>
          <Label>Role</Label>
          <Select
            value={form.role}
            onChange={(e) =>
              setForm({ ...form, role: e.target.value as UserCreateRequest["role"] })
            }
          >
            <option value="viewer">Viewer — read-only</option>
            <option value="operator">Operator — can drive sessions</option>
            <option value="admin">Admin — full access</option>
          </Select>
        </div>
        <div>
          <Label>Temporary password</Label>
          <div className="flex items-center gap-2">
            <Input
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              className="font-mono text-sm"
            />
            <Button variant="ghost" size="sm" onClick={copyPassword} title="Copy">
              <Copy className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setForm({ ...form, password: randomTempPassword() })}
              title="Regenerate"
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
          </div>
          <p className="mt-1 text-xs text-fg-muted">
            Share this with the user — they can log in and change it later.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-fg-primary">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
            checked={form.is_active}
            onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
          />
          Active (can sign in)
        </label>
        {error && <FormError message={error} />}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            <UserPlus className="h-4 w-4" />
            {submitting ? "Creating…" : "Create user"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}


// ---------------------------------------------------------------------------
// New invite modal
// ---------------------------------------------------------------------------


function NewInviteModal({
  open,
  onClose,
  orgId,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  orgId: string | null;
  onCreated: (resp: InviteCreatedResponse, mode?: MintedInviteMode) => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<InviteCreateRequest>({
    email: "",
    role: "viewer",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) {
      setForm({ email: "", role: "viewer" });
      setError("");
    }
  }, [open]);

  const submit = useCallback(async () => {
    if (!orgId) {
      setError("No active organization — refresh and try again.");
      return;
    }
    if (!form.email) {
      setError("Email is required");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const resp = await createInvite(orgId, form);
      toast.success(
        resp.email_sent
          ? "Invite sent — link also available below"
          : "Invite created — copy the link below",
      );
      onCreated(resp);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [form, onCreated, orgId, toast]);

  return (
    <Modal open={open} onClose={onClose} title="New invite">
      <div className="space-y-3">
        <div>
          <Label>Email</Label>
          <Input
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            placeholder="teammate@company.com"
            autoFocus
          />
        </div>
        <div>
          <Label>Role</Label>
          <Select
            value={form.role}
            onChange={(e) =>
              setForm({
                ...form,
                role: e.target.value as InviteCreateRequest["role"],
              })
            }
          >
            <option value="viewer">Viewer — read-only</option>
            <option value="operator">Operator — can drive sessions</option>
            <option value="admin">Admin — full access</option>
          </Select>
        </div>
        {error && <FormError message={error} />}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            <PlusCircle className="h-4 w-4" />
            {submitting ? "Sending…" : "Send invite"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}


// ---------------------------------------------------------------------------
// Bulk invite modal
// ---------------------------------------------------------------------------


function BulkInviteModal({
  open,
  onClose,
  orgId,
  onCompleted,
}: {
  open: boolean;
  onClose: () => void;
  orgId: string | null;
  onCompleted: (result: BulkInviteResult) => void;
}) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) {
      setValue("");
      setError("");
    }
  }, [open]);

  const submit = useCallback(async () => {
    if (!orgId) {
      setError("No active organization — refresh and try again.");
      return;
    }
    const parsed = parseBulkInviteLines(value);
    if (parsed.entries.length === 0 && parsed.failures.length === 0) {
      setError("Paste at least one line.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const successes: InviteCreatedResponse[] = [];
      const failures = [...parsed.failures];

      for (const entry of parsed.entries) {
        try {
          const resp = await createInvite(orgId, {
            email: entry.email,
            role: entry.role,
          });
          successes.push(resp);
        } catch (err) {
          failures.push({
            lineNumber: entry.lineNumber,
            raw: `${entry.email}, ${entry.role}`,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }

      if (successes.length === 0) {
        setError("No invites were created. Fix the lines below and try again.");
        return;
      }

      toast.success(
        failures.length === 0
          ? `Created ${successes.length} invite${successes.length === 1 ? "" : "s"}`
          : `Created ${successes.length} invite${successes.length === 1 ? "" : "s"}; ${failures.length} line${failures.length === 1 ? "" : "s"} failed`,
      );
      onCompleted({ successes, failures });
    } finally {
      setSubmitting(false);
    }
  }, [onCompleted, orgId, toast, value]);

  const preview = useMemo(() => parseBulkInviteLines(value), [value]);

  return (
    <Modal open={open} onClose={onClose} title="Bulk import invites">
      <div className="space-y-4">
        <div>
          <Label>Paste one invite per line</Label>
          <p className="mb-2 text-sm text-fg-muted">
            Format: <code className="font-mono text-xs">email, role</code>
          </p>
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="block min-h-48 w-full rounded-md border border-border-strong bg-bg-input px-3 py-2 font-mono text-sm text-fg-primary placeholder:text-fg-muted transition-colors focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            placeholder={"alice@example.com, operator\nbob@example.com, viewer"}
            autoFocus
          />
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-md border border-border-default bg-bg-elevated p-3 text-sm">
            <div className="font-medium text-fg-primary">Valid lines</div>
            <div className="mt-1 text-fg-secondary">{preview.entries.length}</div>
          </div>
          <div className="rounded-md border border-border-default bg-bg-elevated p-3 text-sm">
            <div className="font-medium text-fg-primary">Invalid lines</div>
            <div className="mt-1 text-fg-secondary">{preview.failures.length}</div>
          </div>
        </div>

        {preview.failures.length > 0 ? (
          <div className="max-h-32 space-y-1 overflow-auto rounded-md border border-status-high-border bg-status-high-bg/40 p-3 text-sm">
            {preview.failures.map((failure) => (
              <div key={`${failure.lineNumber}-${failure.raw}`} className="text-status-high">
                Line {failure.lineNumber}: {failure.error}
              </div>
            ))}
          </div>
        ) : null}

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            <FileSpreadsheet className="h-4 w-4" />
            {submitting ? "Importing…" : "Create invites"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}


// ---------------------------------------------------------------------------
// Minted-invite modal — shown once after a successful create
// ---------------------------------------------------------------------------


function MintedInviteModal({
  invite,
  onClose,
}: {
  invite:
    | {
        mode: MintedInviteMode;
        payload: InviteCreatedResponse;
      }
    | null;
  onClose: () => void;
}) {
  const toast = useToast();
  const open = invite !== null;
  const payload = invite?.payload ?? null;
  const mode = invite?.mode ?? "created";

  const copy = useCallback(async () => {
    if (!payload) return;
    try {
      await navigator.clipboard.writeText(payload.url);
      toast.success("Invite link copied");
    } catch {
      toast.error("Copy failed — select and copy manually.");
    }
  }, [payload, toast]);

  if (!payload) return null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={mode === "resent" ? "Invite resent" : "Invite created"}
    >
      <div className="space-y-4">
        <p className="text-sm text-fg-secondary">
          {mode === "resent" ? "Share the new link with " : "Share this link with "}
          <span className="font-medium text-fg-primary">
            {payload.invite.email}
          </span>{" "}
          — it expires {fmtDate(payload.invite.expires_at)}.
        </p>

        <div className="space-y-2 rounded-md border border-border-default bg-bg-elevated p-3">
          <Label className="text-[10px] uppercase tracking-wide">
            One-time accept URL
          </Label>
          <div className="flex items-center gap-2">
            <code className="flex-1 truncate font-mono text-xs text-fg-primary">
              {payload.url}
            </code>
            <Button variant="ghost" size="sm" onClick={copy}>
              <Copy className="h-4 w-4" /> Copy
            </Button>
          </div>
        </div>

        {payload.email_sent ? (
          <p className="text-sm text-status-low">
            ✓ An invite email was also sent to {payload.invite.email}.
          </p>
        ) : payload.email_error ? (
          <p className="text-sm text-status-high">
            Email delivery failed: {payload.email_error}. The copy-paste link above still works.
          </p>
        ) : (
          <p className="text-sm text-fg-muted">
            SMTP is not configured — share the link via Slack, email, or whatever channel you prefer.
          </p>
        )}

        <div className="flex justify-end pt-2">
          <Button onClick={onClose}>Done</Button>
        </div>
      </div>
    </Modal>
  );
}


// ---------------------------------------------------------------------------
// Bulk invite results
// ---------------------------------------------------------------------------


function BulkInviteResultModal({
  result,
  onClose,
}: {
  result: BulkInviteResult | null;
  onClose: () => void;
}) {
  const toast = useToast();
  const open = result !== null;

  const copy = useCallback(
    async (value: string, label: string) => {
      try {
        await navigator.clipboard.writeText(value);
        toast.success(`${label} copied`);
      } catch {
        toast.error("Copy failed — select and copy manually.");
      }
    },
    [toast],
  );

  if (!result) return null;

  return (
    <Modal open={open} onClose={onClose} title="Bulk invite results">
      <div className="space-y-4">
        <p className="text-sm text-fg-secondary">
          Created{" "}
          <span className="font-medium text-fg-primary">{result.successes.length}</span>{" "}
          invite{result.successes.length === 1 ? "" : "s"}
          {result.failures.length > 0 ? (
            <>
              {" "}and{" "}
              <span className="font-medium text-fg-primary">{result.failures.length}</span>{" "}
              line{result.failures.length === 1 ? "" : "s"} failed.
            </>
          ) : null}
        </p>

        {result.successes.length > 0 ? (
          <div className="space-y-2">
            <Label className="mb-0">Created invites</Label>
            <div className="max-h-72 space-y-3 overflow-auto rounded-md border border-border-default bg-bg-elevated p-3">
              {result.successes.map((item) => (
                <div
                  key={item.invite.id}
                  className="space-y-2 rounded-md border border-border-default bg-bg-surface p-3"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="font-medium text-fg-primary">
                        {item.invite.email}
                      </div>
                      <div className="text-sm text-fg-muted">
                        {item.invite.role} · expires {fmtDate(item.invite.expires_at)}
                      </div>
                    </div>
                    <Badge variant={ROLE_VARIANT[item.invite.role] as never}>
                      {item.invite.role}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 truncate font-mono text-xs text-fg-primary">
                      {item.url}
                    </code>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void copy(item.url, "Invite link")}
                    >
                      <Copy className="h-4 w-4" /> Copy
                    </Button>
                  </div>
                  {item.email_sent ? (
                    <p className="text-sm text-status-low">
                      ✓ Invite email sent.
                    </p>
                  ) : item.email_error ? (
                    <p className="text-sm text-status-high">
                      Email delivery failed: {item.email_error}
                    </p>
                  ) : (
                    <p className="text-sm text-fg-muted">
                      SMTP is not configured — share the link manually.
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {result.failures.length > 0 ? (
          <div className="space-y-2">
            <Label className="mb-0">Failed lines</Label>
            <div className="max-h-48 space-y-2 overflow-auto rounded-md border border-status-high-border bg-status-high-bg/40 p-3 text-sm">
              {result.failures.map((failure) => (
                <div key={`${failure.lineNumber}-${failure.raw}`}>
                  <div className="font-medium text-status-high">
                    Line {failure.lineNumber}: {failure.error}
                  </div>
                  <div className="font-mono text-fg-muted">{failure.raw}</div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="flex justify-end pt-2">
          <Button onClick={onClose}>Done</Button>
        </div>
      </div>
    </Modal>
  );
}
