"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Building2,
  Globe,
  Pencil,
  Plus,
  Star,
  Trash2,
  Users,
  X,
} from "lucide-react";
import {
  addUserToOrganization,
  createOrganization,
  createOrganizationDomain,
  deleteOrganization,
  deleteOrganizationDomain,
  listOrganizationDomains,
  listOrganizations,
  listOrganizationUsers,
  removeUserFromOrganization,
  setPrimaryOrganizationDomain,
  updateOrganization,
  listUsers,
} from "@/lib/api";
import type {
  OrganizationDomainResponse,
  OrganizationResponse,
  UserOrganizationResponse,
  UserResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { FormError, Input, Label, Select } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { CardSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function OrgModal({
  open,
  org,
  onClose,
  onSaved,
}: {
  open: boolean;
  org: OrganizationResponse | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setName(org?.name ?? "");
      setSlug(org?.slug ?? "");
      setError("");
    }
  }, [open, org]);

  if (!open) return null;

  async function handleSubmit() {
    if (!name.trim()) {
      setError("Name is required");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const payload = {
        name: name.trim(),
        slug: slug.trim() || undefined,
      };
      if (org) {
        await updateOrganization(org.id, payload);
      } else {
        await createOrganization(payload);
      }
      await onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={org ? "Edit Organization" : "Create Organization"}
    >
      <div className="space-y-4">
        <div>
          <Label htmlFor="org-name">Organization Name</Label>
          <Input
            id="org-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Acme Corp"
          />
        </div>
        <div>
          <Label htmlFor="org-slug">Slug (Optional)</Label>
          <Input
            id="org-slug"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="acme-corp"
          />
          <p className="mt-1 text-xs text-fg-secondary">
            Leave blank to auto-generate from the name.
          </p>
        </div>

        <FormError message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            {org ? "Save Changes" : "Create Organization"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function UsersModal({
  open,
  org,
  onClose,
}: {
  open: boolean;
  org: OrganizationResponse | null;
  onClose: () => void;
}) {
  const [members, setMembers] = useState<UserOrganizationResponse[]>([]);
  const [allUsers, setAllUsers] = useState<UserResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  const [selectedUser, setSelectedUser] = useState("");
  const [selectedRole, setSelectedRole] = useState<"admin" | "operator" | "viewer">("viewer");
  const [adding, setAdding] = useState(false);

  const loadData = useCallback(async () => {
    if (!org) return;
    setLoading(true);
    try {
      const [membersRes, usersRes] = await Promise.all([
        listOrganizationUsers(org.id),
        listUsers({ limit: 1000 }), // Fetch all users for the dropdown
      ]);
      setMembers(membersRes.items);
      setAllUsers(usersRes.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, [org]);

  useEffect(() => {
    if (open) {
      loadData();
      setSelectedUser("");
      setSelectedRole("viewer");
      setError("");
    }
  }, [open, loadData]);

  if (!open || !org) return null;

  async function handleAddUser() {
    if (!selectedUser) return;
    setAdding(true);
    setError("");
    try {
      await addUserToOrganization(org!.id, {
        user_id: selectedUser,
        role: selectedRole,
      });
      setSelectedUser("");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add user");
    } finally {
      setAdding(false);
    }
  }

  async function handleRemoveUser(userId: string) {
    if (!confirm("Are you sure you want to remove this user from the organization?")) return;
    setError("");
    try {
      await removeUserFromOrganization(org!.id, userId);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove user");
    }
  }

  // Filter out users who are already members
  const memberIds = new Set(members.map((m) => m.user_id));
  const availableUsers = allUsers.filter((u) => !memberIds.has(u.id));

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Manage Users: ${org.name}`}
      maxWidth="max-w-2xl"
    >
      <div className="space-y-6">
        <FormError message={error} />

        {/* Add User Form */}
        <div className="flex items-end gap-3 rounded-lg border border-border-subtle bg-bg-muted p-4">
          <div className="flex-1">
            <Label htmlFor="add-user">User</Label>
            <Select
              id="add-user"
              value={selectedUser}
              onChange={(e) => setSelectedUser(e.target.value)}
            >
              <option value="">Select a user...</option>
              {availableUsers.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username} ({u.email})
                </option>
              ))}
            </Select>
          </div>
          <div className="w-40">
            <Label htmlFor="add-role">Org Role</Label>
            <Select
              id="add-role"
              value={selectedRole}
              onChange={(e) => setSelectedRole(e.target.value as any)}
            >
              <option value="admin">Admin</option>
              <option value="operator">Operator</option>
              <option value="viewer">Viewer</option>
            </Select>
          </div>
          <Button onClick={handleAddUser} disabled={!selectedUser} loading={adding}>
            Add User
          </Button>
        </div>

        {/* User List */}
        <div>
          <h3 className="mb-3 text-sm font-semibold text-fg-primary">Organization Members ({members.length})</h3>
          {loading ? (
            <div className="animate-pulse space-y-2">
              <div className="h-10 rounded-md bg-bg-muted" />
              <div className="h-10 rounded-md bg-bg-muted" />
            </div>
          ) : members.length === 0 ? (
            <p className="text-sm text-fg-secondary">No members in this organization.</p>
          ) : (
            <div className="divide-y divide-border-subtle rounded-md border border-border-subtle">
              {members.map((m) => (
                <div key={m.user_id} className="flex items-center justify-between p-3">
                  <div>
                    <p className="text-sm font-medium text-fg-primary">{m.username}</p>
                    <p className="text-xs text-fg-muted">{m.email}</p>
                  </div>
                  <div className="flex items-center gap-4">
                    <span className="rounded-pill bg-bg-muted px-2 py-0.5 text-xs font-medium uppercase text-fg-secondary">
                      {m.role}
                    </span>
                    <button
                      onClick={() => handleRemoveUser(m.user_id)}
                      className="text-fg-muted hover:text-status-error transition-colors"
                      title="Remove User"
                    >
                      <X size={16} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex justify-end pt-4 border-t border-border-subtle">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function DomainsModal({
  open,
  org,
  onClose,
}: {
  open: boolean;
  org: OrganizationResponse | null;
  onClose: () => void;
}) {
  const [domains, setDomains] = useState<OrganizationDomainResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [newDomain, setNewDomain] = useState("");
  const [newPrimary, setNewPrimary] = useState(false);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    if (!org) return;
    setLoading(true);
    setError("");
    try {
      const res = await listOrganizationDomains(org.id);
      setDomains(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load domains");
    } finally {
      setLoading(false);
    }
  }, [org]);

  useEffect(() => {
    if (open) {
      load();
      setNewDomain("");
      setNewPrimary(false);
      setError("");
    }
  }, [open, load]);

  if (!open || !org) return null;

  async function handleAdd() {
    if (!newDomain.trim()) return;
    setAdding(true);
    setError("");
    try {
      await createOrganizationDomain(org!.id, {
        domain: newDomain.trim(),
        is_primary: newPrimary,
      });
      setNewDomain("");
      setNewPrimary(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add domain");
    } finally {
      setAdding(false);
    }
  }

  async function handleSetPrimary(d: OrganizationDomainResponse) {
    try {
      await setPrimaryOrganizationDomain(org!.id, d.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to set primary");
    }
  }

  async function handleDelete(d: OrganizationDomainResponse) {
    if (!confirm(`Remove domain "${d.domain}"? Requests on this host will stop being routed to this org.`)) return;
    try {
      await deleteOrganizationDomain(org!.id, d.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete domain");
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Domains: ${org.name}`}
      maxWidth="max-w-2xl"
    >
      <div className="space-y-6">
        <p className="text-xs text-fg-secondary">
          Each domain pins this organization to a hostname. Requests served on a registered host
          are forced into this tenant; users not in this org will be denied. Make sure DNS resolves
          the host to this AIM deployment.
        </p>

        <FormError message={error} />

        <div className="flex items-end gap-3 rounded-lg border border-border-subtle bg-bg-muted p-4">
          <div className="flex-1">
            <Label htmlFor="new-domain">Add domain</Label>
            <Input
              id="new-domain"
              value={newDomain}
              onChange={(e) => setNewDomain(e.target.value)}
              placeholder="acme.aim.example.com"
            />
          </div>
          <label className="flex items-center gap-2 pb-2 text-xs text-fg-secondary">
            <input
              type="checkbox"
              checked={newPrimary}
              onChange={(e) => setNewPrimary(e.target.checked)}
            />
            Primary
          </label>
          <Button onClick={handleAdd} disabled={!newDomain.trim()} loading={adding}>
            Add
          </Button>
        </div>

        <div>
          <h3 className="mb-3 text-sm font-semibold text-fg-primary">
            Registered Domains ({domains.length})
          </h3>
          {loading ? (
            <div className="animate-pulse space-y-2">
              <div className="h-10 rounded-md bg-bg-muted" />
              <div className="h-10 rounded-md bg-bg-muted" />
            </div>
          ) : domains.length === 0 ? (
            <p className="text-sm text-fg-secondary">No domains registered.</p>
          ) : (
            <div className="divide-y divide-border-subtle rounded-md border border-border-subtle">
              {domains.map((d) => (
                <div key={d.id} className="flex items-center justify-between p-3">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm text-fg-primary">{d.domain}</p>
                    <p className="text-xs text-fg-muted">
                      {d.verified ? "Verified" : "Unverified"} · added{" "}
                      {fmtDate(d.created_at)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {d.is_primary ? (
                      <span className="flex items-center gap-1 rounded-pill bg-status-low-bg px-2 py-0.5 text-xs font-medium text-status-low">
                        <Star size={12} /> Primary
                      </span>
                    ) : (
                      <button
                        onClick={() => handleSetPrimary(d)}
                        className="text-xs text-fg-secondary hover:text-fg-primary transition-colors"
                        title="Set as primary"
                      >
                        Make primary
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(d)}
                      className="text-fg-muted hover:text-status-error transition-colors"
                      title="Remove domain"
                    >
                      <X size={16} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex justify-end pt-4 border-t border-border-subtle">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export default function OrganizationsPage() {
  const { user } = useAuth();
  const isSuperAdmin = user?.role === "admin";
  
  const [orgs, setOrgs] = useState<OrganizationResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const toast = useToast();

  const [showOrgModal, setShowOrgModal] = useState(false);
  const [editingOrg, setEditingOrg] = useState<OrganizationResponse | null>(null);

  const [showUsersModal, setShowUsersModal] = useState(false);
  const [managingUsersOrg, setManagingUsersOrg] = useState<OrganizationResponse | null>(null);

  const [showDomainsModal, setShowDomainsModal] = useState(false);
  const [managingDomainsOrg, setManagingDomainsOrg] = useState<OrganizationResponse | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await listOrganizations();
      setOrgs(res.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load organizations");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (!isSuperAdmin) {
      setLoading(false);
      return;
    }
    load();
  }, [isSuperAdmin, load]);

  if (!isSuperAdmin) {
    return (
      <EmptyState
        icon={Building2}
        title="Access Denied"
        description="Only global administrators can manage organizations."
      />
    );
  }

  async function handleDelete(org: OrganizationResponse) {
    if (!confirm(`Are you sure you want to delete organization "${org.name}"? This action cannot be undone.`)) {
      return;
    }
    try {
      await deleteOrganization(org.id);
      toast.success(`Deleted organization "${org.name}"`);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <CardSkeleton lines={2} />
        <CardSkeleton lines={3} />
        <CardSkeleton lines={3} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-fg-primary">Organizations</h1>
          <p className="mt-1 text-sm text-fg-secondary">
            Manage multi-tenant organizations and their members.
          </p>
        </div>
        <Button
          onClick={() => {
            setEditingOrg(null);
            setShowOrgModal(true);
          }}
        >
          <Plus size={14} /> New Organization
        </Button>
      </div>

      {orgs.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No organizations"
          description="Create an organization to get started."
          action={
            <Button
              onClick={() => {
                setEditingOrg(null);
                setShowOrgModal(true);
              }}
            >
              <Plus size={14} /> Create Organization
            </Button>
          }
        />
      ) : (
        <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm divide-y divide-border-subtle">
          {orgs.map((org) => (
            <div key={org.id} className="flex items-center justify-between p-6">
              <div>
                <h3 className="text-base font-semibold text-fg-primary">{org.name}</h3>
                <p className="text-sm text-fg-secondary">Slug: {org.slug}</p>
                <p className="mt-1 text-xs text-fg-muted">
                  Created {fmtDate(org.created_at)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setManagingUsersOrg(org);
                    setShowUsersModal(true);
                  }}
                >
                  <Users size={14} /> Manage Users
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setManagingDomainsOrg(org);
                    setShowDomainsModal(true);
                  }}
                >
                  <Globe size={14} /> Domains
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setEditingOrg(org);
                    setShowOrgModal(true);
                  }}
                >
                  <Pencil size={14} /> Edit
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => handleDelete(org)}
                >
                  <Trash2 size={14} />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <OrgModal
        open={showOrgModal}
        org={editingOrg}
        onClose={() => setShowOrgModal(false)}
        onSaved={load}
      />

      <UsersModal
        open={showUsersModal}
        org={managingUsersOrg}
        onClose={() => setShowUsersModal(false)}
      />

      <DomainsModal
        open={showDomainsModal}
        org={managingDomainsOrg}
        onClose={() => setShowDomainsModal(false)}
      />
    </div>
  );
}
