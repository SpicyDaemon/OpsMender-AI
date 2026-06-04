"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  Copy,
  KeyRound,
  Trash2,
  UserCheck,
  UserX,
} from "lucide-react";

import {
  getUser,
  getUserDeletePreconditions,
  mintPasswordReset,
  setTemporaryPassword,
  softDeleteUser,
  updateUser,
} from "@/lib/api";
import type {
  PasswordResetMintResponse,
  SoftDeletePreconditions,
  UserResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { FormError, Label, Select } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";


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
    return {
      label: value,
      variant: "medium" as const,
      href: user.primary_org_id
        ? `/dashboard/organizations?org=${user.primary_org_id}&auth=oidc`
        : "/dashboard/organizations",
    };
  }
  if (value.startsWith("saml:")) {
    return {
      label: value,
      variant: "default" as const,
      href: user.primary_org_id
        ? `/dashboard/organizations?org=${user.primary_org_id}&auth=saml`
        : "/dashboard/organizations",
    };
  }
  return { label: "local", variant: "low" as const, href: null };
}


export default function PersonDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="mx-auto max-w-3xl py-12 text-fg-muted">Loading…</div>
      }
    >
      <PersonDetailGuard />
    </Suspense>
  );
}


function PersonDetailGuard() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  if (!isAdmin) {
    return (
      <div className="mx-auto max-w-3xl py-12">
        <EmptyState
          icon={AlertTriangle}
          title="Admin only"
          description="The People surface is restricted to administrators."
        />
      </div>
    );
  }

  return <PersonDetail />;
}


function PersonDetail() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user: actor } = useAuth();
  const toast = useToast();
  const id = searchParams.get("id") ?? "";

  const [target, setTarget] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  const reload = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const u = await getUser(id);
      setTarget(u);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.toLowerCase().includes("not found") || msg.includes("404")) {
        setNotFound(true);
      } else {
        toast.error(msg);
      }
    } finally {
      setLoading(false);
    }
  }, [id, toast]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (loading && !target) {
    return (
      <div className="mx-auto max-w-3xl py-12 text-fg-muted">Loading…</div>
    );
  }

  if (notFound || !target) {
    return <UserNotFound />;
  }

  if (target.deleted_at) {
    return <DeletedUserView user={target} />;
  }

  const isSelf = actor?.id === target.id;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title={target.username}
        subtitle={target.email}
        actions={
          <Link
            href="/dashboard/people"
            className="inline-flex items-center gap-1 text-sm text-fg-secondary hover:text-fg-primary"
          >
            <ArrowLeft className="h-4 w-4" /> People
          </Link>
        }
      />

      <SummaryCard user={target} />

      <RoleEditor user={target} onSaved={reload} disabled={isSelf} />

      <ActiveToggle user={target} onSaved={reload} disabled={isSelf} />

      <ActionsCard
        user={target}
        onChanged={reload}
        onDeleted={() => router.push("/dashboard/people")}
        isSelf={isSelf}
      />
    </div>
  );
}


function UserNotFound() {
  return (
    <div className="mx-auto max-w-3xl py-12">
      <EmptyState
        icon={UserX}
        title="User no longer in OpsMender"
        description="This account either never existed or has been deleted. Past incidents and sessions still show the original username, but the account itself has been removed."
        action={
          <Link
            href="/dashboard/people"
            className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline"
          >
            <ArrowLeft className="h-4 w-4" /> Back to People
          </Link>
        }
      />
    </div>
  );
}


function DeletedUserView({ user }: { user: UserResponse }) {
  return (
    <div className="mx-auto max-w-3xl py-12">
      <EmptyState
        icon={UserX}
        title="User no longer in OpsMender"
        description={`${user.username}'s account was deleted ${user.deleted_at ? fmtDate(user.deleted_at) : ""}. Past incidents and sessions still show this username, but the account itself has been removed.`}
        action={
          <Link
            href="/dashboard/people"
            className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline"
          >
            <ArrowLeft className="h-4 w-4" /> Back to People
          </Link>
        }
      />
    </div>
  );
}


function SummaryCard({ user }: { user: UserResponse }) {
  const authMethod = authMethodMeta(user);
  return (
    <section className="rounded-lg border border-border-default bg-bg-panel p-5">
      <div className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <Field label="Username" value={user.username} mono />
        <Field label="Email" value={user.email} mono />
        <Field
          label="Role"
          value={user.role}
          badge={user.role === "admin" ? "high" : user.role === "operator" ? "medium" : "default"}
        />
        <Field
          label="Status"
          value={user.is_active ? "Active" : "Inactive"}
          badge={user.is_active ? "low" : "default"}
        />
        <Field
          label="Auth method"
          value={authMethod.label}
          badge={authMethod.variant}
          href={authMethod.href}
        />
        <Field label="Joined" value={fmtDate(user.created_at)} />
        <Field label="User ID" value={user.id} mono small />
      </div>
    </section>
  );
}


function Field({
  label,
  value,
  mono,
  small,
  badge,
  href,
}: {
  label: string;
  value: string;
  mono?: boolean;
  small?: boolean;
  badge?: string;
  href?: string | null;
}) {
  const body = badge ? (
    <Badge variant={badge as never}>{value}</Badge>
  ) : (
    <p
      className={`${mono ? "font-mono" : "font-medium"} ${
        small ? "text-xs" : "text-sm"
      } text-fg-primary`}
    >
      {value}
    </p>
  );
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
        {label}
      </p>
      {href ? (
        <Link href={href} className="inline-flex hover:opacity-80">
          {body}
        </Link>
      ) : (
        body
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Role editor
// ---------------------------------------------------------------------------


function RoleEditor({
  user,
  onSaved,
  disabled,
}: {
  user: UserResponse;
  onSaved: () => void | Promise<void>;
  disabled: boolean;
}) {
  const toast = useToast();
  const [role, setRole] = useState<UserResponse["role"]>(user.role);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setRole(user.role);
  }, [user.role]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await updateUser(user.id, { role });
      toast.success("Role updated");
      await onSaved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [onSaved, role, toast, user.id]);

  return (
    <section className="rounded-lg border border-border-default bg-bg-panel p-5">
      <h2 className="text-sm font-semibold text-fg-primary">Role</h2>
      <p className="mt-1 text-xs text-fg-muted">
        Admin — full access. Operator — drive sessions + approve. Viewer — read-only.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 sm:min-w-[200px]">
          <Label>Global role</Label>
          <Select
            value={role}
            onChange={(e) => setRole(e.target.value as UserResponse["role"])}
            disabled={disabled || saving}
          >
            <option value="viewer">Viewer</option>
            <option value="operator">Operator</option>
            <option value="admin">Admin</option>
          </Select>
        </div>
        <Button
          onClick={save}
          disabled={disabled || saving || role === user.role}
        >
          {saving ? "Saving…" : "Save role"}
        </Button>
      </div>
      {disabled && (
        <p className="mt-3 text-xs text-fg-muted">
          You cannot change your own role.
        </p>
      )}
    </section>
  );
}


// ---------------------------------------------------------------------------
// Activate / deactivate toggle
// ---------------------------------------------------------------------------


function ActiveToggle({
  user,
  onSaved,
  disabled,
}: {
  user: UserResponse;
  onSaved: () => void | Promise<void>;
  disabled: boolean;
}) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);

  const toggle = useCallback(async () => {
    setSaving(true);
    try {
      await updateUser(user.id, { is_active: !user.is_active });
      toast.success(user.is_active ? "User deactivated" : "User reactivated");
      await onSaved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [onSaved, toast, user.id, user.is_active]);

  return (
    <section className="rounded-lg border border-border-default bg-bg-panel p-5">
      <h2 className="text-sm font-semibold text-fg-primary">
        {user.is_active ? "Deactivate account" : "Reactivate account"}
      </h2>
      <p className="mt-1 text-xs text-fg-muted">
        {user.is_active
          ? "Deactivated users cannot log in, are removed from on-call resolution and paging, and can't be selected for rosters. Their roster history and data stay in place. Required before deletion."
          : "Reactivated users can log in again with their existing password and become eligible for on-call once more."}
      </p>
      <div className="mt-3">
        <Button
          variant={user.is_active ? "secondary" : "primary"}
          onClick={toggle}
          disabled={disabled || saving}
        >
          {user.is_active ? (
            <>
              <UserX className="h-4 w-4" /> Deactivate
            </>
          ) : (
            <>
              <UserCheck className="h-4 w-4" /> Reactivate
            </>
          )}
        </Button>
      </div>
      {disabled && (
        <p className="mt-3 text-xs text-fg-muted">
          You cannot deactivate your own account.
        </p>
      )}
    </section>
  );
}


// ---------------------------------------------------------------------------
// Reset password + soft delete
// ---------------------------------------------------------------------------


function ActionsCard({
  user,
  onChanged,
  onDeleted,
  isSelf,
}: {
  user: UserResponse;
  onChanged: () => void | Promise<void>;
  onDeleted: () => void;
  isSelf: boolean;
}) {
  const toast = useToast();
  const [resetting, setResetting] = useState(false);
  const [minted, setMinted] = useState<PasswordResetMintResponse | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [tempPw, setTempPw] = useState<string | null>(null);
  const [tempLoading, setTempLoading] = useState(false);

  const mint = useCallback(async () => {
    setResetting(true);
    try {
      const resp = await mintPasswordReset(user.id);
      setMinted(resp);
      toast.success(
        resp.email_sent
          ? "Reset email sent — link also available below"
          : "Reset link minted — copy it from the modal",
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setResetting(false);
    }
  }, [toast, user.id]);

  const makeTempPassword = useCallback(async () => {
    setTempLoading(true);
    setTempPw(null);
    try {
      const resp = await setTemporaryPassword(user.id);
      setTempPw(resp.temporary_password);
      toast.success("Temporary password set — copy it now (shown once)");
      await onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setTempLoading(false);
    }
  }, [toast, user.id, onChanged]);

  return (
    <section className="space-y-4 rounded-lg border border-border-default bg-bg-panel p-5">
      <div>
        <h2 className="text-sm font-semibold text-fg-primary">Password</h2>
        <p className="mt-1 text-xs text-fg-muted">
          <strong>Email reset:</strong> mint a one-time URL the user pastes into their browser to set a new password (expires in 24 hours).
          {" "}
          <strong>Manual reset:</strong> set a temporary password (shown once) — the user logs in with it and is forced to change it.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={mint} disabled={resetting} variant="secondary">
            <KeyRound className="h-4 w-4" />
            {resetting ? "Minting…" : "Send password reset"}
          </Button>
          <Button onClick={makeTempPassword} disabled={tempLoading} variant="secondary">
            <KeyRound className="h-4 w-4" />
            {tempLoading ? "Setting…" : "Set temporary password"}
          </Button>
        </div>
        {tempPw && (
          <div className="mt-3 rounded-md border border-border-default bg-bg-elevated p-3">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
              Temporary password (shown once)
            </p>
            <div className="mt-1 flex items-center gap-2">
              <code className="flex-1 truncate font-mono text-sm text-fg-primary">{tempPw}</code>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  navigator.clipboard
                    .writeText(tempPw)
                    .then(() => toast.success("Copied"))
                    .catch(() => toast.error("Copy failed"));
                }}
              >
                Copy
              </Button>
            </div>
            <p className="mt-1 text-xs text-fg-muted">
              The user must change this on next sign-in.
            </p>
          </div>
        )}
      </div>

      <hr className="border-border-subtle" />

      <div>
        <h2 className="text-sm font-semibold text-status-high">Danger zone</h2>
        <p className="mt-1 text-xs text-fg-muted">
          Soft-delete this account. The username stays on past incidents for context, but the user can no longer log in and the email is freed up for future invites.
        </p>
        <div className="mt-3">
          <Button
            variant="secondary"
            onClick={() => setDeleteOpen(true)}
            disabled={isSelf}
          >
            <Trash2 className="h-4 w-4" /> Delete user
          </Button>
        </div>
        {isSelf && (
          <p className="mt-2 text-xs text-fg-muted">
            You cannot delete your own account.
          </p>
        )}
      </div>

      <MintedResetModal minted={minted} onClose={() => setMinted(null)} />
      <DeleteUserModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        user={user}
        onDeleted={() => {
          setDeleteOpen(false);
          onDeleted();
        }}
      />
    </section>
  );
}


function MintedResetModal({
  minted,
  onClose,
}: {
  minted: PasswordResetMintResponse | null;
  onClose: () => void;
}) {
  const toast = useToast();
  const open = minted !== null;

  const copy = useCallback(async () => {
    if (!minted) return;
    try {
      await navigator.clipboard.writeText(minted.url);
      toast.success("Reset link copied");
    } catch {
      toast.error("Copy failed — select and copy manually.");
    }
  }, [minted, toast]);

  if (!minted) return null;

  return (
    <Modal open={open} onClose={onClose} title="Password reset minted">
      <div className="space-y-4">
        <p className="text-sm text-fg-secondary">
          The link expires {fmtDate(minted.expires_at)}.
        </p>
        <div className="space-y-2 rounded-md border border-border-default bg-bg-elevated p-3">
          <Label className="text-[10px] uppercase tracking-wide">
            One-time reset URL
          </Label>
          <div className="flex items-center gap-2">
            <code className="flex-1 truncate font-mono text-xs text-fg-primary">
              {minted.url}
            </code>
            <Button variant="ghost" size="sm" onClick={copy}>
              <Copy className="h-4 w-4" /> Copy
            </Button>
          </div>
        </div>
        {minted.email_sent ? (
          <p className="text-sm text-status-low">
            ✓ An email with this link was also sent.
          </p>
        ) : minted.email_error ? (
          <p className="text-sm text-status-high">
            Email delivery failed: {minted.email_error}. The copy-paste link above still works.
          </p>
        ) : (
          <p className="text-sm text-fg-muted">
            SMTP is not configured — share the link via Slack or another channel.
          </p>
        )}
        <div className="flex justify-end pt-2">
          <Button onClick={onClose}>Done</Button>
        </div>
      </div>
    </Modal>
  );
}


function DeleteUserModal({
  open,
  onClose,
  user,
  onDeleted,
}: {
  open: boolean;
  onClose: () => void;
  user: UserResponse;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const [pre, setPre] = useState<SoftDeletePreconditions | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    void (async () => {
      try {
        const p = await getUserDeletePreconditions(user.id);
        if (!cancelled) setPre(p);
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, user.id]);

  const submit = useCallback(async () => {
    setSubmitting(true);
    setError("");
    try {
      await softDeleteUser(user.id);
      toast.success("User deleted");
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [onDeleted, toast, user.id]);

  return (
    <Modal open={open} onClose={onClose} title={`Delete ${user.username}?`}>
      <div className="space-y-4">
        {loading ? (
          <p className="text-sm text-fg-muted">Checking prerequisites…</p>
        ) : pre ? (
          <>
            <PreconditionRow
              label="Account is deactivated"
              ok={!pre.is_active}
              detail={
                pre.is_active
                  ? "Use ‘Deactivate’ above first."
                  : "Deactivated."
              }
            />
            <PreconditionRow
              label="Removed from all rosters"
              ok={pre.roster_memberships === 0}
              detail={
                pre.roster_memberships === 0
                  ? "Not on any rosters."
                  : `Still on ${pre.roster_memberships} roster${
                      pre.roster_memberships === 1 ? "" : "s"
                    }. Visit Paging → Rosters to remove them.`
              }
            />
            {pre.can_delete && (
              <p className="text-sm text-fg-secondary">
                Deletion is irreversible. The username will stay visible on past incidents and sessions; the email is scrubbed so it can be re-invited later.
              </p>
            )}
          </>
        ) : null}
        {error && <FormError message={error} />}
        <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={!pre?.can_delete || submitting || loading}
          >
            <Trash2 className="h-4 w-4" />
            {submitting ? "Deleting…" : "Delete user"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}


function PreconditionRow({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail: string;
}) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-border-subtle bg-bg-elevated p-3">
      <span
        className={`mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-xs font-semibold ${
          ok
            ? "bg-status-low-bg text-status-low"
            : "bg-status-high-bg text-status-high"
        }`}
      >
        {ok ? "✓" : "!"}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-fg-primary">{label}</p>
        <p className="mt-0.5 text-xs text-fg-secondary">{detail}</p>
      </div>
    </div>
  );
}
