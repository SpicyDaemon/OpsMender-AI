"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Copy, Mail, PlusCircle, Trash2, UserPlus, Users } from "lucide-react";

import {
  createInvite,
  listInvites,
  listUsers,
  revokeInvite,
} from "@/lib/api";
import type {
  InviteCreateRequest,
  InviteCreatedResponse,
  InviteResponse,
  InviteStatus,
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
  const [newInviteOpen, setNewInviteOpen] = useState(false);
  const [mintedInvite, setMintedInvite] =
    useState<InviteCreatedResponse | null>(null);

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

  const onInviteCreated = useCallback(
    (resp: InviteCreatedResponse) => {
      setMintedInvite(resp);
      setNewInviteOpen(false);
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

  return (
    <div className="mx-auto max-w-6xl">
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
        <UsersTab users={users} loading={usersLoading} />
      ) : (
        <InvitesTab
          invites={invites}
          loading={invitesLoading}
          onNew={() => setNewInviteOpen(true)}
          onRevoke={onRevoke}
        />
      )}

      <NewInviteModal
        open={newInviteOpen}
        onClose={() => setNewInviteOpen(false)}
        orgId={orgId}
        onCreated={onInviteCreated}
      />

      <MintedInviteModal
        invite={mintedInvite}
        onClose={() => setMintedInvite(null)}
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
}: {
  users: UserResponse[];
  loading: boolean;
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
      {
        id: "auth_source",
        label: "Auth method",
        accessor: (u) => {
          if (u.auth_source.startsWith("oidc:")) return "oidc";
          if (u.auth_source.startsWith("saml:")) return "saml";
          return "local";
        },
        cell: (u) => <AuthMethodBadge user={u} />,
        sortable: true,
        filterChips: {
          options: [
            { value: "local", label: "Local" },
            { value: "oidc", label: "OIDC" },
            { value: "saml", label: "SAML" },
          ],
          valueOf: (u) => {
            if (u.auth_source.startsWith("oidc:")) return "oidc";
            if (u.auth_source.startsWith("saml:")) return "saml";
            return "local";
          },
        },
      },
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
    [],
  );

  if (loading && users.length === 0) return <TableSkeleton rows={6} columns={5} />;
  if (users.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No users yet"
        description="Invite your first teammate from the Invites tab."
      />
    );
  }

  return (
    <DataTable
      rows={users}
      columns={columns}
      rowKey={(u) => u.id}
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
  onRevoke,
}: {
  invites: InviteResponse[];
  loading: boolean;
  onNew: () => void;
  onRevoke: (invite: InviteResponse) => void;
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
        <div className="flex justify-end">
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
      rowKey={(i) => i.id}
      storageKey="opsmender:people-invites-table"
      searchPlaceholder="Search by email…"
      dateRangeColumn={{
        id: "created_at",
        label: "Sent",
        valueOf: (i) => i.created_at,
      }}
      toolbarRight={
        <Button onClick={onNew}>
          <UserPlus className="h-4 w-4" /> New invite
        </Button>
      }
      rowActions={(invite) =>
        invite.status === "pending" ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onRevoke(invite)}
            title="Revoke"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        ) : null
      }
    />
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
  onCreated: (resp: InviteCreatedResponse) => void;
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
// Minted-invite modal — shown once after a successful create
// ---------------------------------------------------------------------------


function MintedInviteModal({
  invite,
  onClose,
}: {
  invite: InviteCreatedResponse | null;
  onClose: () => void;
}) {
  const toast = useToast();
  const open = invite !== null;

  const copy = useCallback(async () => {
    if (!invite) return;
    try {
      await navigator.clipboard.writeText(invite.url);
      toast.success("Invite link copied");
    } catch {
      toast.error("Copy failed — select and copy manually.");
    }
  }, [invite, toast]);

  if (!invite) return null;

  return (
    <Modal open={open} onClose={onClose} title="Invite created">
      <div className="space-y-4">
        <p className="text-sm text-fg-secondary">
          Share this link with{" "}
          <span className="font-medium text-fg-primary">
            {invite.invite.email}
          </span>{" "}
          — it expires {fmtDate(invite.invite.expires_at)}.
        </p>

        <div className="space-y-2 rounded-md border border-border-default bg-bg-elevated p-3">
          <Label className="text-[10px] uppercase tracking-wide">
            One-time accept URL
          </Label>
          <div className="flex items-center gap-2">
            <code className="flex-1 truncate font-mono text-xs text-fg-primary">
              {invite.url}
            </code>
            <Button variant="ghost" size="sm" onClick={copy}>
              <Copy className="h-4 w-4" /> Copy
            </Button>
          </div>
        </div>

        {invite.email_sent ? (
          <p className="text-sm text-status-low">
            ✓ An invite email was also sent to {invite.invite.email}.
          </p>
        ) : invite.email_error ? (
          <p className="text-sm text-status-high">
            Email delivery failed: {invite.email_error}. The copy-paste link above still works.
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
