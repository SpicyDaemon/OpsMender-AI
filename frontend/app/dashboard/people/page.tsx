"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Copy,
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
import { formatDateTime } from "@/lib/formatDate";


type MintedInviteMode = "created" | "resent";


const ROLE_VARIANT: Record<UserResponse["role"], string> = {
  admin: "high",
  operator: "medium",
  viewer: "default",
};


function fmtDate(iso: string): string {
  return formatDateTime(iso);
}


function authMethodMeta(user: UserResponse) {
  const value = user.auth_source || "local";
  if (value.startsWith("oidc:")) {
    const slug = value.slice("oidc:".length) || "org";
    return {
      label: `oidc:${slug}`,
      variant: "medium" as const,
      href: "/dashboard/config#organization-auth",
    };
  }
  if (value.startsWith("saml:")) {
    const slug = value.slice("saml:".length) || "org";
    return {
      label: `saml:${slug}`,
      variant: "default" as const,
      href: "/dashboard/config#organization-auth",
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

  const [users, setUsers] = useState<UserResponse[]>([]);
  const [invites, setInvites] = useState<InviteResponse[]>([]);
  const [usersLoading, setUsersLoading] = useState(true);
  const [invitesLoading, setInvitesLoading] = useState(true);
  // v1 is local-auth only by default. Surface SSO/SAML affordances (the auth
  // method column + filter) only when advanced auth is enabled or already
  // configured — matching the D-027 rule used elsewhere.
  const [advancedAuth, setAdvancedAuth] = useState(false);
  const [newUserOpen, setNewUserOpen] = useState(false);
  const [mintedInvite, setMintedInvite] = useState<{
    mode: MintedInviteMode;
    payload: InviteCreatedResponse;
  } | null>(null);

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

      <PeopleTable
        users={users}
        invites={invites}
        loading={usersLoading || invitesLoading}
        advancedAuth={advancedAuth}
        onNewPerson={() => setNewUserOpen(true)}
        onRevoke={onRevoke}
        onResend={onResend}
      />

      <NewPersonModal
        open={newUserOpen}
        onClose={() => setNewUserOpen(false)}
        orgId={orgId}
        onUserCreated={() => {
          setNewUserOpen(false);
          void reloadUsers();
        }}
        onInviteCreated={(resp) => {
          setNewUserOpen(false);
          onInviteCreated(resp);
        }}
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


/**
 * Unified People table: real users + pending invites in one list with statuses
 * Active / Inactive / Invited (Part 2). One "New user" action opens the tabbed
 * create/invite modal; invite rows expose resend/revoke.
 */
type PersonRow =
  | { kind: "user"; id: string; user: UserResponse }
  | { kind: "invite"; id: string; invite: InviteResponse };

function personName(row: PersonRow): string {
  if (row.kind === "user") {
    const u = row.user;
    const full = `${u.first_name ?? ""} ${u.last_name ?? ""}`.trim();
    return full || u.username;
  }
  return row.invite.email;
}

function personRole(row: PersonRow): UserResponse["role"] {
  return row.kind === "user" ? row.user.role : row.invite.role;
}

function personStatus(row: PersonRow): "active" | "inactive" | "invited" {
  if (row.kind === "invite") return "invited";
  return row.user.is_active ? "active" : "inactive";
}

function PeopleTable({
  users,
  invites,
  loading,
  advancedAuth,
  onNewPerson,
  onRevoke,
  onResend,
}: {
  users: UserResponse[];
  invites: InviteResponse[];
  loading: boolean;
  advancedAuth: boolean;
  onNewPerson: () => void;
  onRevoke: (invite: InviteResponse) => void;
  onResend: (invite: InviteResponse) => void;
}) {
  const rows = useMemo<PersonRow[]>(() => {
    const userRows: PersonRow[] = users.map((u) => ({ kind: "user", id: u.id, user: u }));
    const inviteRows: PersonRow[] = invites
      .filter((i) => i.status === "pending")
      .map((i) => ({ kind: "invite", id: `invite-${i.id}`, invite: i }));
    return [...userRows, ...inviteRows];
  }, [users, invites]);

  const columns = useMemo<DataTableColumn<PersonRow>[]>(
    () => [
      {
        id: "name",
        label: "Name",
        accessor: (r) => `${personName(r)} ${r.kind === "user" ? r.user.email : r.invite.email}`,
        cell: (r) =>
          r.kind === "user" ? (
            <div>
              <Link
                href={`/dashboard/people/detail?id=${r.user.id}`}
                className="font-medium text-fg-primary hover:text-accent-text"
              >
                {personName(r)}
              </Link>
              <p className="text-xs text-fg-muted">{r.user.email}</p>
            </div>
          ) : (
            <div>
              <span className="font-medium text-fg-primary">{personName(r)}</span>
              <p className="text-xs text-fg-muted">Invitation pending</p>
            </div>
          ),
        sortable: true,
        searchable: true,
      },
      {
        id: "role",
        label: "Role",
        accessor: (r) => personRole(r),
        cell: (r) => <Badge variant={ROLE_VARIANT[personRole(r)] as never}>{personRole(r)}</Badge>,
        sortable: true,
        filterChips: {
          options: [
            { value: "admin", label: "Admin" },
            { value: "operator", label: "Operator" },
            { value: "viewer", label: "Viewer" },
          ],
          valueOf: (r) => personRole(r),
        },
      },
      {
        id: "status",
        label: "Status",
        accessor: (r) => personStatus(r),
        cell: (r) => {
          const s = personStatus(r);
          if (s === "active") return <Badge variant="low">Active</Badge>;
          if (s === "inactive") return <Badge variant="default">Inactive</Badge>;
          return <Badge variant="medium">Invited</Badge>;
        },
        sortable: true,
        filterChips: {
          options: [
            { value: "active", label: "Active" },
            { value: "inactive", label: "Inactive" },
            { value: "invited", label: "Invited" },
          ],
          valueOf: (r) => personStatus(r),
        },
      },
      ...(advancedAuth
        ? [
            {
              id: "auth_source",
              label: "Auth method",
              accessor: (r: PersonRow) =>
                r.kind === "user"
                  ? r.user.auth_source.startsWith("oidc:")
                    ? "oidc"
                    : r.user.auth_source.startsWith("saml:")
                      ? "saml"
                      : "local"
                  : "local",
              cell: (r: PersonRow) =>
                r.kind === "user" ? <AuthMethodBadge user={r.user} /> : <Badge variant="low">local</Badge>,
              sortable: true,
              filterChips: {
                options: [
                  { value: "local", label: "Local" },
                  { value: "oidc", label: "OIDC" },
                  { value: "saml", label: "SAML" },
                ],
                valueOf: (r: PersonRow) =>
                  r.kind === "user" && r.user.auth_source.startsWith("oidc:")
                    ? "oidc"
                    : r.kind === "user" && r.user.auth_source.startsWith("saml:")
                      ? "saml"
                      : "local",
              },
            } as DataTableColumn<PersonRow>,
          ]
        : []),
      {
        id: "when",
        label: "Joined / Sent",
        accessor: (r) => (r.kind === "user" ? r.user.created_at : r.invite.created_at),
        cell: (r) => (
          <span className="whitespace-nowrap text-sm text-fg-secondary">
            {r.kind === "user"
              ? fmtDate(r.user.created_at)
              : `Sent ${fmtDate(r.invite.created_at)}`}
            {r.kind === "invite" && (
              <span className="block text-[11px] text-fg-muted">
                Expires {fmtDate(r.invite.expires_at)}
              </span>
            )}
          </span>
        ),
        sortable: true,
      },
    ],
    [advancedAuth],
  );

  if (loading && rows.length === 0) return <TableSkeleton rows={6} columns={5} />;
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No people yet"
        description="Create a user directly or invite a teammate by email."
        action={
          <Button onClick={onNewPerson}>
            <UserPlus className="h-4 w-4" /> New user
          </Button>
        }
      />
    );
  }

  return (
    <DataTable
      rows={rows}
      columns={columns}
      filterBar
      toolbarRight={
        <Button onClick={onNewPerson}>
          <UserPlus className="h-4 w-4" /> New user
        </Button>
      }
      rowKey={(r) => r.id}
      storageKey="opsmender:people-table"
      searchPlaceholder="Search by name, username, or email…"
      rowActions={(r) =>
        r.kind === "user" ? (
          <Link
            href={`/dashboard/people/detail?id=${r.user.id}`}
            className="text-sm font-medium text-accent-text hover:underline"
          >
            Manage
          </Link>
        ) : (
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={() => onResend(r.invite)} title="Resend">
              <RotateCcw className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onRevoke(r.invite)} title="Revoke">
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        )
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


function NewPersonModal({
  open,
  onClose,
  orgId,
  onUserCreated,
  onInviteCreated,
}: {
  open: boolean;
  onClose: () => void;
  orgId: string | null;
  onUserCreated: () => void;
  onInviteCreated: (resp: InviteCreatedResponse, mode?: MintedInviteMode) => void;
}) {
  const [tab, setTab] = useState<"create" | "invite">("create");

  useEffect(() => {
    if (open) setTab("create");
  }, [open]);

  return (
    <Modal open={open} onClose={onClose} title="New user">
      <div className="mb-4 flex gap-2">
        {(
          [
            { id: "create", label: "Create user" },
            { id: "invite", label: "Invite user" },
          ] as const
        ).map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-current={tab === id ? "page" : undefined}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
              tab === id
                ? "bg-accent text-accent-contrast"
                : "border border-border-default bg-bg-surface text-fg-secondary hover:text-fg-primary"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "create" ? (
        <CreateUserForm onClose={onClose} onCreated={onUserCreated} />
      ) : (
        <InviteUserForm onClose={onClose} orgId={orgId} onCreated={onInviteCreated} />
      )}
    </Modal>
  );
}

function CreateUserForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<UserCreateRequest>({
    username: "",
    email: "",
    role: "operator",
    password: randomTempPassword(),
    is_active: true,
    first_name: "",
    last_name: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

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
        first_name: (form.first_name ?? "").trim() || null,
        last_name: (form.last_name ?? "").trim() || null,
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
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <Label>First name</Label>
            <Input
              value={form.first_name ?? ""}
              onChange={(e) => setForm({ ...form, first_name: e.target.value })}
              placeholder="Jane"
              autoFocus
            />
          </div>
          <div>
            <Label>Last name</Label>
            <Input
              value={form.last_name ?? ""}
              onChange={(e) => setForm({ ...form, last_name: e.target.value })}
              placeholder="Doe"
            />
          </div>
          <div>
            <Label>Username</Label>
            <Input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              placeholder="jdoe"
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
            className="h-4 w-4 rounded border-border-strong text-accent-text focus:ring-accent"
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
  );
}


// ---------------------------------------------------------------------------
// Invite-user form (tab in NewPersonModal)
// ---------------------------------------------------------------------------


function InviteUserForm({
  onClose,
  orgId,
  onCreated,
}: {
  onClose: () => void;
  orgId: string | null;
  onCreated: (resp: InviteCreatedResponse, mode?: MintedInviteMode) => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<InviteCreateRequest>({
    email: "",
    role: "viewer",
    first_name: "",
    last_name: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

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
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>First name <span className="text-fg-muted">(optional)</span></Label>
            <Input
              value={form.first_name ?? ""}
              onChange={(e) => setForm({ ...form, first_name: e.target.value })}
              placeholder="Ada"
            />
          </div>
          <div>
            <Label>Last name <span className="text-fg-muted">(optional)</span></Label>
            <Input
              value={form.last_name ?? ""}
              onChange={(e) => setForm({ ...form, last_name: e.target.value })}
              placeholder="Lovelace"
            />
          </div>
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
