"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Building2,
  FileKey,
  Globe,
  KeyRound,
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
  deleteOrgSAMLConfig,
  deleteOrgSSOConfig,
  getOrgSAMLConfig,
  getOrgSSOConfig,
  listOrganizationDomains,
  listOrganizations,
  listOrganizationUsers,
  removeUserFromOrganization,
  setPrimaryOrganizationDomain,
  updateOrganization,
  upsertOrgSAMLConfig,
  upsertOrgSSOConfig,
  listUsers,
} from "@/lib/api";
import type {
  OrganizationDomainResponse,
  OrganizationResponse,
  OrgSAMLConfigResponse,
  OrgSSOConfigResponse,
  UserOrganizationResponse,
  UserResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
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

  const memberColumns = useMemo<DataTableColumn<UserOrganizationResponse>[]>(
    () => [
      {
        id: "username",
        label: "User",
        accessor: (m) => m.username,
        sortable: true,
        searchable: true,
        cell: (m) => (
          <div>
            <p className="text-sm font-medium text-fg-primary">{m.username}</p>
            <p className="text-xs text-fg-muted">{m.email}</p>
          </div>
        ),
      },
      {
        id: "role",
        label: "Role",
        accessor: (m) => m.role,
        sortable: true,
        filterChips: {
          options: [
            { value: "admin", label: "Admin" },
            { value: "operator", label: "Operator" },
            { value: "viewer", label: "Viewer" },
          ],
          valueOf: (m) => m.role,
        },
        cell: (m) => (
          <span className="rounded-pill bg-bg-muted px-2 py-0.5 text-xs font-medium uppercase text-fg-secondary">
            {m.role}
          </span>
        ),
      },
    ],
    [],
  );

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
          <h3 className="mb-3 text-sm font-semibold text-fg-primary">
            Organization Members ({members.length})
          </h3>
          {loading ? (
            <div className="animate-pulse space-y-2">
              <div className="h-10 rounded-md bg-bg-muted" />
              <div className="h-10 rounded-md bg-bg-muted" />
            </div>
          ) : (
            <DataTable
              rows={members}
              columns={memberColumns}
              rowKey={(m) => m.user_id}
              searchPlaceholder="Search by username or email…"
              defaultPageSize={10}
              pageSizeOptions={[10, 25, 50]}
              empty={
                <p className="text-sm text-fg-secondary">
                  No members in this organization.
                </p>
              }
              rowActions={(m) => (
                <button
                  onClick={() => handleRemoveUser(m.user_id)}
                  className="text-fg-muted hover:text-status-error transition-colors"
                  title="Remove User"
                >
                  <X size={16} />
                </button>
              )}
            />
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

  const domainColumns = useMemo<DataTableColumn<OrganizationDomainResponse>[]>(
    () => [
      {
        id: "domain",
        label: "Domain",
        accessor: (d) => d.domain,
        sortable: true,
        searchable: true,
        cell: (d) => (
          <div className="min-w-[12rem]">
            <p className="truncate font-mono text-sm text-fg-primary">{d.domain}</p>
            <p className="text-xs text-fg-muted">
              {d.verified ? "Verified" : "Unverified"} · added{" "}
              {fmtDate(d.created_at)}
            </p>
          </div>
        ),
      },
      {
        id: "verified",
        label: "Status",
        accessor: (d) => (d.verified ? "verified" : "unverified"),
        sortable: true,
        filterChips: {
          options: [
            { value: "verified", label: "Verified" },
            { value: "unverified", label: "Unverified" },
          ],
          valueOf: (d) => (d.verified ? "verified" : "unverified"),
        },
        cell: (d) =>
          d.verified ? (
            <span className="rounded-pill bg-status-low-bg px-2 py-0.5 text-xs font-medium text-status-low">
              Verified
            </span>
          ) : (
            <span className="rounded-pill bg-bg-muted px-2 py-0.5 text-xs font-medium text-fg-secondary">
              Unverified
            </span>
          ),
      },
      {
        id: "primary",
        label: "Primary",
        accessor: (d) => (d.is_primary ? "primary" : "alternate"),
        sortable: true,
        filterChips: {
          options: [
            { value: "primary", label: "Primary" },
            { value: "alternate", label: "Alternate" },
          ],
          valueOf: (d) => (d.is_primary ? "primary" : "alternate"),
        },
        cell: (d) =>
          d.is_primary ? (
            <span className="flex items-center gap-1 rounded-pill bg-status-low-bg px-2 py-0.5 text-xs font-medium text-status-low">
              <Star size={12} /> Primary
            </span>
          ) : (
            <span className="text-xs text-fg-muted">—</span>
          ),
      },
    ],
    [],
  );

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
          the host to this OpsMender deployment.
        </p>

        <FormError message={error} />

        <div className="flex items-end gap-3 rounded-lg border border-border-subtle bg-bg-muted p-4">
          <div className="flex-1">
            <Label htmlFor="new-domain">Add domain</Label>
            <Input
              id="new-domain"
              value={newDomain}
              onChange={(e) => setNewDomain(e.target.value)}
              placeholder="acme.opsmender.example.com"
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
          ) : (
            <DataTable
              rows={domains}
              columns={domainColumns}
              rowKey={(d) => d.id}
              searchPlaceholder="Search domains…"
              defaultPageSize={10}
              pageSizeOptions={[10, 25, 50]}
              empty={
                <p className="text-sm text-fg-secondary">No domains registered.</p>
              }
              rowActions={(d) => (
                <div className="flex items-center gap-2">
                  {!d.is_primary && (
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
              )}
            />
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

function SSOModal({
  open,
  org,
  onClose,
}: {
  open: boolean;
  org: OrganizationResponse | null;
  onClose: () => void;
}) {
  const [existing, setExisting] = useState<OrgSSOConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const [discoveryUrl, setDiscoveryUrl] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [scopes, setScopes] = useState("openid email profile");
  const [emailClaim, setEmailClaim] = useState("email");
  const [nameClaim, setNameClaim] = useState("name");
  const [defaultRole, setDefaultRole] = useState<"admin" | "operator" | "viewer">("viewer");
  const [allowedDomains, setAllowedDomains] = useState("");
  const [isActive, setIsActive] = useState(true);

  const load = useCallback(async () => {
    if (!org) return;
    setLoading(true);
    setError("");
    try {
      const cfg = await getOrgSSOConfig(org.id);
      setExisting(cfg);
      setDiscoveryUrl(cfg.discovery_url);
      setClientId(cfg.client_id);
      setClientSecret("");
      setScopes(cfg.scopes);
      setEmailClaim(cfg.email_claim);
      setNameClaim(cfg.name_claim);
      setDefaultRole(cfg.default_role);
      setAllowedDomains(cfg.allowed_email_domains ?? "");
      setIsActive(cfg.is_active);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (/not\s+found|404/i.test(msg)) {
        setExisting(null);
        setDiscoveryUrl("");
        setClientId("");
        setClientSecret("");
        setScopes("openid email profile");
        setEmailClaim("email");
        setNameClaim("name");
        setDefaultRole("viewer");
        setAllowedDomains("");
        setIsActive(true);
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  }, [org]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  if (!open || !org) return null;

  async function handleSave() {
    setSaving(true);
    setError("");
    try {
      await upsertOrgSSOConfig(org!.id, {
        provider: "oidc",
        discovery_url: discoveryUrl.trim(),
        client_id: clientId.trim(),
        client_secret: clientSecret ? clientSecret : null,
        scopes: scopes.trim() || "openid email profile",
        email_claim: emailClaim.trim() || "email",
        name_claim: nameClaim.trim() || "name",
        default_role: defaultRole,
        allowed_email_domains: allowedDomains.trim() || null,
        is_active: isActive,
      });
      await load();
      setClientSecret("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Disable SSO for this organization?")) return;
    setSaving(true);
    try {
      await deleteOrgSSOConfig(org!.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`SSO: ${org.name}`}
      maxWidth="max-w-2xl"
    >
      <div className="space-y-4">
        <p className="text-xs text-fg-secondary">
          Configure an OIDC identity provider (Okta, Azure AD, Google Workspace, Auth0, Keycloak).
          OpsMender redirects users to the IdP and JIT-provisions accounts on first login. The login URL
          for this org is{" "}
          <code className="font-mono text-fg-primary">/auth/sso/{org.slug}/login</code>.
        </p>

        <FormError message={error} />

        {loading ? (
          <div className="animate-pulse space-y-2">
            <div className="h-10 rounded-md bg-bg-muted" />
            <div className="h-10 rounded-md bg-bg-muted" />
          </div>
        ) : (
          <>
            <div>
              <Label htmlFor="sso-discovery">OIDC Discovery URL</Label>
              <Input
                id="sso-discovery"
                value={discoveryUrl}
                onChange={(e) => setDiscoveryUrl(e.target.value)}
                placeholder="https://idp.example.com/.well-known/openid-configuration"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="sso-client-id">Client ID</Label>
                <Input
                  id="sso-client-id"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  placeholder="opsmender-app"
                />
              </div>
              <div>
                <Label htmlFor="sso-secret">
                  Client Secret {existing ? "(leave blank to keep)" : ""}
                </Label>
                <Input
                  id="sso-secret"
                  type="password"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                  placeholder={existing?.has_client_secret ? "••••••••" : "supersecret"}
                />
              </div>
            </div>
            <div>
              <Label htmlFor="sso-scopes">Scopes</Label>
              <Input
                id="sso-scopes"
                value={scopes}
                onChange={(e) => setScopes(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="sso-email-claim">Email claim</Label>
                <Input
                  id="sso-email-claim"
                  value={emailClaim}
                  onChange={(e) => setEmailClaim(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="sso-name-claim">Name claim</Label>
                <Input
                  id="sso-name-claim"
                  value={nameClaim}
                  onChange={(e) => setNameClaim(e.target.value)}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="sso-default-role">Default role for new users</Label>
                <Select
                  id="sso-default-role"
                  value={defaultRole}
                  onChange={(e) => setDefaultRole(e.target.value as any)}
                >
                  <option value="viewer">Viewer</option>
                  <option value="operator">Operator</option>
                  <option value="admin">Admin</option>
                </Select>
              </div>
              <div>
                <Label htmlFor="sso-allowed-domains">
                  Allowed email domains (optional, comma-separated)
                </Label>
                <Input
                  id="sso-allowed-domains"
                  value={allowedDomains}
                  onChange={(e) => setAllowedDomains(e.target.value)}
                  placeholder="acme.com,acme.co.uk"
                />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm text-fg-secondary">
              <input
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
              />
              Enabled — show "Sign in with SSO" on the login page
            </label>
          </>
        )}

        <div className="flex justify-between gap-2 border-t border-border-subtle pt-4">
          <div>
            {existing && (
              <Button variant="danger" onClick={handleDelete} disabled={saving}>
                Disable SSO
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleSave} loading={saving}>
              {existing ? "Save Changes" : "Enable SSO"}
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function SAMLModal({
  open,
  org,
  onClose,
}: {
  open: boolean;
  org: OrganizationResponse | null;
  onClose: () => void;
}) {
  const [existing, setExisting] = useState<OrgSAMLConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  // Source toggle: paste a metadata URL (preferred) or paste raw XML.
  const [source, setSource] = useState<"url" | "xml">("url");
  const [metadataUrl, setMetadataUrl] = useState("");
  const [metadataXml, setMetadataXml] = useState("");
  const [emailAttr, setEmailAttr] = useState(
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
  );
  const [nameAttr, setNameAttr] = useState(
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
  );
  const [defaultRole, setDefaultRole] = useState<"admin" | "operator" | "viewer">("viewer");
  const [allowedDomains, setAllowedDomains] = useState("");
  const [wantAssertionsSigned, setWantAssertionsSigned] = useState(true);
  const [wantResponseSigned, setWantResponseSigned] = useState(true);
  const [isActive, setIsActive] = useState(true);

  const load = useCallback(async () => {
    if (!org) return;
    setLoading(true);
    setError("");
    try {
      const cfg = await getOrgSAMLConfig(org.id);
      setExisting(cfg);
      if (cfg.idp_metadata_url) {
        setSource("url");
        setMetadataUrl(cfg.idp_metadata_url);
        setMetadataXml("");
      } else {
        setSource("xml");
        setMetadataUrl("");
        // The backend never returns the raw XML, only a flag — leave the
        // textarea empty so admins consciously paste a fresh value if they
        // want to overwrite.
        setMetadataXml("");
      }
      setEmailAttr(cfg.email_attribute);
      setNameAttr(cfg.name_attribute);
      setDefaultRole(cfg.default_role);
      setAllowedDomains(cfg.allowed_email_domains ?? "");
      setWantAssertionsSigned(cfg.want_assertions_signed);
      setWantResponseSigned(cfg.want_response_signed);
      setIsActive(cfg.is_active);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (/not\s+found|404/i.test(msg)) {
        setExisting(null);
        setSource("url");
        setMetadataUrl("");
        setMetadataXml("");
        setEmailAttr("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress");
        setNameAttr("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name");
        setDefaultRole("viewer");
        setAllowedDomains("");
        setWantAssertionsSigned(true);
        setWantResponseSigned(true);
        setIsActive(true);
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  }, [org]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  if (!open || !org) return null;

  async function handleSave() {
    setSaving(true);
    setError("");
    try {
      const url = metadataUrl.trim();
      const xml = metadataXml.trim();
      if (source === "url") {
        if (!url) {
          setError("Provide an IdP metadata URL.");
          setSaving(false);
          return;
        }
      } else {
        // For XML mode: when editing an existing config, allow leaving the
        // textarea blank to keep the stored value (the response never echoes
        // it back). For new configs, require a paste.
        if (!xml && !existing?.has_idp_metadata_xml) {
          setError("Paste the IdP metadata XML.");
          setSaving(false);
          return;
        }
      }
      await upsertOrgSAMLConfig(org!.id, {
        is_active: isActive,
        idp_metadata_url: source === "url" ? url : null,
        idp_metadata_xml: source === "xml" && xml ? xml : null,
        email_attribute: emailAttr.trim(),
        name_attribute: nameAttr.trim(),
        default_role: defaultRole,
        allowed_email_domains: allowedDomains.trim() || null,
        want_assertions_signed: wantAssertionsSigned,
        want_response_signed: wantResponseSigned,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Disable SAML for this organization?")) return;
    setSaving(true);
    try {
      await deleteOrgSAMLConfig(org!.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`SAML: ${org.name}`}
      maxWidth="max-w-2xl"
    >
      <div className="space-y-4">
        <p className="text-xs text-fg-secondary">
          Configure a SAML 2.0 identity provider (older Okta tenants, ADFS,
          classic Azure AD enterprise apps). OpsMender redirects users to the IdP
          and JIT-provisions accounts on first login. The login URL for this
          org is{" "}
          <code className="font-mono text-fg-primary">/auth/saml/{org.slug}/login</code>
          . The IdP-facing SP metadata URL is{" "}
          <code className="font-mono text-fg-primary">/auth/saml/{org.slug}/metadata</code>
          .
        </p>

        <FormError message={error} />

        {loading ? (
          <div className="animate-pulse space-y-2">
            <div className="h-10 rounded-md bg-bg-muted" />
            <div className="h-10 rounded-md bg-bg-muted" />
          </div>
        ) : (
          <>
            <div>
              <Label htmlFor="saml-source">IdP metadata source</Label>
              <Select
                id="saml-source"
                value={source}
                onChange={(e) => setSource(e.target.value as "url" | "xml")}
              >
                <option value="url">Metadata URL (preferred)</option>
                <option value="xml">Paste raw XML</option>
              </Select>
            </div>
            {source === "url" ? (
              <div>
                <Label htmlFor="saml-md-url">IdP metadata URL</Label>
                <Input
                  id="saml-md-url"
                  value={metadataUrl}
                  onChange={(e) => setMetadataUrl(e.target.value)}
                  placeholder="https://idp.example.com/app/metadata"
                />
              </div>
            ) : (
              <div>
                <Label htmlFor="saml-md-xml">
                  IdP metadata XML
                  {existing?.has_idp_metadata_xml
                    ? " (leave blank to keep stored XML)"
                    : ""}
                </Label>
                <textarea
                  id="saml-md-xml"
                  value={metadataXml}
                  onChange={(e) => setMetadataXml(e.target.value)}
                  rows={8}
                  className="w-full rounded-md border border-border-strong bg-bg-base px-3 py-2 font-mono text-xs text-fg-primary"
                  placeholder="<EntityDescriptor …>"
                />
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="saml-email-attr">Email attribute</Label>
                <Input
                  id="saml-email-attr"
                  value={emailAttr}
                  onChange={(e) => setEmailAttr(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="saml-name-attr">Name attribute</Label>
                <Input
                  id="saml-name-attr"
                  value={nameAttr}
                  onChange={(e) => setNameAttr(e.target.value)}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="saml-default-role">
                  Default role for new users
                </Label>
                <Select
                  id="saml-default-role"
                  value={defaultRole}
                  onChange={(e) => setDefaultRole(e.target.value as any)}
                >
                  <option value="viewer">Viewer</option>
                  <option value="operator">Operator</option>
                  <option value="admin">Admin</option>
                </Select>
              </div>
              <div>
                <Label htmlFor="saml-allowed-domains">
                  Allowed email domains (optional, comma-separated)
                </Label>
                <Input
                  id="saml-allowed-domains"
                  value={allowedDomains}
                  onChange={(e) => setAllowedDomains(e.target.value)}
                  placeholder="acme.com,acme.co.uk"
                />
              </div>
            </div>
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm text-fg-secondary">
                <input
                  type="checkbox"
                  checked={wantAssertionsSigned}
                  onChange={(e) => setWantAssertionsSigned(e.target.checked)}
                />
                Require signed assertions
              </label>
              <label className="flex items-center gap-2 text-sm text-fg-secondary">
                <input
                  type="checkbox"
                  checked={wantResponseSigned}
                  onChange={(e) => setWantResponseSigned(e.target.checked)}
                />
                Require signed responses
              </label>
              <label className="flex items-center gap-2 text-sm text-fg-secondary">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                />
                Enabled — show "Sign in with SAML" on the login page
              </label>
            </div>
          </>
        )}

        <div className="flex justify-between gap-2 border-t border-border-subtle pt-4">
          <div>
            {existing && (
              <Button variant="danger" onClick={handleDelete} disabled={saving}>
                Disable SAML
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleSave} loading={saving}>
              {existing ? "Save Changes" : "Enable SAML"}
            </Button>
          </div>
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

  const [showSSOModal, setShowSSOModal] = useState(false);
  const [managingSSOOrg, setManagingSSOOrg] = useState<OrganizationResponse | null>(null);

  const [showSAMLModal, setShowSAMLModal] = useState(false);
  const [managingSAMLOrg, setManagingSAMLOrg] = useState<OrganizationResponse | null>(null);

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

  useEffect(() => {
    if (!isSuperAdmin || orgs.length === 0 || typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const orgId = params.get("org");
    const auth = params.get("auth");
    if (!orgId || !auth) return;
    const target = orgs.find((item) => item.id === orgId);
    if (!target) return;
    if (auth === "oidc") {
      setManagingSSOOrg(target);
      setShowSSOModal(true);
    } else if (auth === "saml") {
      setManagingSAMLOrg(target);
      setShowSAMLModal(true);
    } else {
      return;
    }
    window.history.replaceState({}, "", "/dashboard/organizations");
  }, [isSuperAdmin, orgs]);

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
                    setManagingSSOOrg(org);
                    setShowSSOModal(true);
                  }}
                >
                  <KeyRound size={14} /> SSO
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setManagingSAMLOrg(org);
                    setShowSAMLModal(true);
                  }}
                >
                  <FileKey size={14} /> SAML
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

      <SSOModal
        open={showSSOModal}
        org={managingSSOOrg}
        onClose={() => setShowSSOModal(false)}
      />

      <SAMLModal
        open={showSAMLModal}
        org={managingSAMLOrg}
        onClose={() => setShowSAMLModal(false)}
      />
    </div>
  );
}
