"use client";

import { useCallback, useEffect, useState } from "react";
import { Globe2, KeyRound, ShieldCheck, Trash2 } from "lucide-react";
import {
  createOrganizationDomain,
  deleteOrgSAMLConfig,
  deleteOrgSSOConfig,
  deleteOrganizationDomain,
  getOrgSAMLConfig,
  getOrgSSOConfig,
  getOrganization,
  listOrganizationDomains,
  setPrimaryOrganizationDomain,
  updateOrganization,
  upsertOrgSAMLConfig,
  upsertOrgSSOConfig,
} from "@/lib/api";
import type {
  OrganizationDomainResponse,
  OrganizationResponse,
  OrgSAMLConfigResponse,
  OrgSSOConfigResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { FormAlert, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";

type Role = "admin" | "operator" | "viewer";

function notFound(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes("404") || message.toLowerCase().includes("not found");
}

export function OrganizationSettingsSection({ orgId }: { orgId: string }) {
  const toast = useToast();
  const [org, setOrg] = useState<OrganizationResponse | null>(null);
  const [domains, setDomains] = useState<OrganizationDomainResponse[]>([]);
  const [sso, setSso] = useState<OrgSSOConfigResponse | null>(null);
  const [saml, setSaml] = useState<OrgSAMLConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [savingOrg, setSavingOrg] = useState(false);
  const [savingDomain, setSavingDomain] = useState(false);
  const [savingSso, setSavingSso] = useState(false);
  const [savingSaml, setSavingSaml] = useState(false);

  const [orgForm, setOrgForm] = useState({
    name: "",
    slug: "",
    company_name: "",
    logo_url: "",
    primary_color: "",
    secondary_color: "",
    favicon_url: "",
  });
  const [newDomain, setNewDomain] = useState("");
  const [ssoForm, setSsoForm] = useState({
    is_active: false,
    discovery_url: "",
    client_id: "",
    client_secret: "",
    scopes: "openid email profile",
    email_claim: "email",
    name_claim: "name",
    default_role: "operator" as Role,
    allowed_email_domains: "",
  });
  const [samlForm, setSamlForm] = useState({
    is_active: false,
    metadataMode: "url" as "url" | "xml",
    idp_metadata_url: "",
    idp_metadata_xml: "",
    email_attribute: "email",
    name_attribute: "name",
    default_role: "operator" as Role,
    allowed_email_domains: "",
    want_assertions_signed: true,
    want_response_signed: true,
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [orgRes, domainRes, ssoRes, samlRes] = await Promise.all([
        getOrganization(orgId),
        listOrganizationDomains(orgId),
        getOrgSSOConfig(orgId).catch((err) => {
          if (notFound(err)) return null;
          throw err;
        }),
        getOrgSAMLConfig(orgId).catch((err) => {
          if (notFound(err)) return null;
          throw err;
        }),
      ]);
      setOrg(orgRes);
      setDomains(domainRes.items);
      setSso(ssoRes);
      setSaml(samlRes);
      setOrgForm({
        name: orgRes.name,
        slug: orgRes.slug,
        company_name: orgRes.branding?.company_name ?? "",
        logo_url: orgRes.branding?.logo_url ?? "",
        primary_color: orgRes.branding?.primary_color ?? "",
        secondary_color: orgRes.branding?.secondary_color ?? "",
        favicon_url: orgRes.branding?.favicon_url ?? "",
      });
      if (ssoRes) {
        setSsoForm({
          is_active: ssoRes.is_active,
          discovery_url: ssoRes.discovery_url,
          client_id: ssoRes.client_id,
          client_secret: "",
          scopes: ssoRes.scopes,
          email_claim: ssoRes.email_claim,
          name_claim: ssoRes.name_claim,
          default_role: ssoRes.default_role,
          allowed_email_domains: ssoRes.allowed_email_domains ?? "",
        });
      }
      if (samlRes) {
        setSamlForm({
          is_active: samlRes.is_active,
          metadataMode: samlRes.idp_metadata_url ? "url" : "xml",
          idp_metadata_url: samlRes.idp_metadata_url ?? "",
          idp_metadata_xml: "",
          email_attribute: samlRes.email_attribute,
          name_attribute: samlRes.name_attribute,
          default_role: samlRes.default_role,
          allowed_email_domains: samlRes.allowed_email_domains ?? "",
          want_assertions_signed: samlRes.want_assertions_signed,
          want_response_signed: samlRes.want_response_signed,
        });
      }
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Failed to load workspace settings.");
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveOrg() {
    setSavingOrg(true);
    setNotice("");
    try {
      const saved = await updateOrganization(orgId, {
        name: orgForm.name.trim(),
        slug: orgForm.slug.trim(),
        branding: {
          company_name: orgForm.company_name.trim() || undefined,
          logo_url: orgForm.logo_url.trim() || undefined,
          primary_color: orgForm.primary_color.trim() || undefined,
          secondary_color: orgForm.secondary_color.trim() || undefined,
          favicon_url: orgForm.favicon_url.trim() || undefined,
        },
      });
      setOrg(saved);
      toast.success("Workspace settings saved");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Save failed.");
    } finally {
      setSavingOrg(false);
    }
  }

  async function addDomain() {
    if (!newDomain.trim()) return;
    setSavingDomain(true);
    setNotice("");
    try {
      await createOrganizationDomain(orgId, { domain: newDomain.trim() });
      setNewDomain("");
      const res = await listOrganizationDomains(orgId);
      setDomains(res.items);
      toast.success("Domain added");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Domain save failed.");
    } finally {
      setSavingDomain(false);
    }
  }

  async function makePrimary(domain: OrganizationDomainResponse) {
    setNotice("");
    try {
      await setPrimaryOrganizationDomain(orgId, domain.id);
      const res = await listOrganizationDomains(orgId);
      setDomains(res.items);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not set primary domain.");
    }
  }

  async function removeDomain(domain: OrganizationDomainResponse) {
    if (!confirm(`Remove ${domain.domain}?`)) return;
    setNotice("");
    try {
      await deleteOrganizationDomain(orgId, domain.id);
      setDomains((items) => items.filter((item) => item.id !== domain.id));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not remove domain.");
    }
  }

  async function saveSso() {
    setSavingSso(true);
    setNotice("");
    try {
      const saved = await upsertOrgSSOConfig(orgId, {
        provider: "oidc",
        is_active: ssoForm.is_active,
        discovery_url: ssoForm.discovery_url.trim(),
        client_id: ssoForm.client_id.trim(),
        client_secret: ssoForm.client_secret.trim() || undefined,
        scopes: ssoForm.scopes.trim() || "openid email profile",
        email_claim: ssoForm.email_claim.trim() || "email",
        name_claim: ssoForm.name_claim.trim() || "name",
        default_role: ssoForm.default_role,
        allowed_email_domains: ssoForm.allowed_email_domains.trim() || null,
      });
      setSso(saved);
      setSsoForm((form) => ({ ...form, client_secret: "" }));
      toast.success("OIDC settings saved");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "OIDC save failed.");
    } finally {
      setSavingSso(false);
    }
  }

  async function disableSso() {
    if (!confirm("Disable OIDC sign-in?")) return;
    setNotice("");
    try {
      await deleteOrgSSOConfig(orgId);
      setSso(null);
      setSsoForm((form) => ({ ...form, is_active: false, client_secret: "" }));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not disable OIDC.");
    }
  }

  async function saveSaml() {
    setSavingSaml(true);
    setNotice("");
    try {
      const saved = await upsertOrgSAMLConfig(orgId, {
        is_active: samlForm.is_active,
        idp_metadata_url:
          samlForm.metadataMode === "url"
            ? samlForm.idp_metadata_url.trim()
            : null,
        idp_metadata_xml:
          samlForm.metadataMode === "xml"
            ? samlForm.idp_metadata_xml.trim()
            : null,
        email_attribute: samlForm.email_attribute.trim() || "email",
        name_attribute: samlForm.name_attribute.trim() || "name",
        default_role: samlForm.default_role,
        allowed_email_domains: samlForm.allowed_email_domains.trim() || null,
        want_assertions_signed: samlForm.want_assertions_signed,
        want_response_signed: samlForm.want_response_signed,
      });
      setSaml(saved);
      setSamlForm((form) => ({
        ...form,
        idp_metadata_xml: "",
        metadataMode: saved.idp_metadata_url ? "url" : "xml",
      }));
      toast.success("SAML settings saved");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "SAML save failed.");
    } finally {
      setSavingSaml(false);
    }
  }

  async function disableSaml() {
    if (!confirm("Disable SAML sign-in?")) return;
    setNotice("");
    try {
      await deleteOrgSAMLConfig(orgId);
      setSaml(null);
      setSamlForm((form) => ({ ...form, is_active: false, idp_metadata_xml: "" }));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not disable SAML.");
    }
  }

  if (loading) {
    return (
      <section className="rounded-xl border border-border-subtle bg-bg-panel p-5 text-sm text-fg-muted">
        Loading workspace settings...
      </section>
    );
  }

  return (
    <section id="organization-auth" className="space-y-5 rounded-xl border border-border-subtle bg-bg-panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-fg-primary">Workspace</h2>
          <p className="mt-1 text-sm text-fg-secondary">
            {org?.name ?? "Current workspace"} is the only active organization for this instance.
          </p>
        </div>
        {org && <Badge variant="info">{org.slug}</Badge>}
      </div>

      <FormAlert message={notice} />

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <Label htmlFor="org-name" required>Name</Label>
          <Input
            id="org-name"
            value={orgForm.name}
            onChange={(e) => setOrgForm({ ...orgForm, name: e.target.value })}
            required
          />
        </div>
        <div>
          <Label htmlFor="org-slug" required>Slug</Label>
          <Input
            id="org-slug"
            value={orgForm.slug}
            onChange={(e) => setOrgForm({ ...orgForm, slug: e.target.value })}
            required
          />
        </div>
        <div>
          <Label htmlFor="company-name">Display name</Label>
          <Input
            id="company-name"
            value={orgForm.company_name}
            onChange={(e) => setOrgForm({ ...orgForm, company_name: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="logo-url">Logo URL</Label>
          <Input
            id="logo-url"
            value={orgForm.logo_url}
            onChange={(e) => setOrgForm({ ...orgForm, logo_url: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="primary-color">Primary color</Label>
          <Input
            id="primary-color"
            value={orgForm.primary_color}
            placeholder="#2563eb"
            onChange={(e) => setOrgForm({ ...orgForm, primary_color: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="secondary-color">Secondary color</Label>
          <Input
            id="secondary-color"
            value={orgForm.secondary_color}
            placeholder="#0f172a"
            onChange={(e) => setOrgForm({ ...orgForm, secondary_color: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="favicon-url">Favicon URL</Label>
          <Input
            id="favicon-url"
            value={orgForm.favicon_url}
            onChange={(e) => setOrgForm({ ...orgForm, favicon_url: e.target.value })}
          />
        </div>
      </div>
      <Button onClick={saveOrg} loading={savingOrg}>
        Save workspace
      </Button>

      <div className="border-t border-border-subtle pt-5">
        <div className="mb-3 flex items-center gap-2">
          <Globe2 size={16} className="text-fg-muted" />
          <h3 className="text-sm font-semibold text-fg-primary">Custom domains</h3>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            value={newDomain}
            placeholder="ops.example.com"
            onChange={(e) => setNewDomain(e.target.value)}
          />
          <Button onClick={addDomain} loading={savingDomain} variant="secondary">
            Add domain
          </Button>
        </div>
        <div className="mt-3 space-y-2">
          {domains.length === 0 ? (
            <p className="text-sm text-fg-muted">No custom domains yet.</p>
          ) : (
            domains.map((domain) => (
              <div
                key={domain.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-fg-primary">{domain.domain}</span>
                  {domain.is_primary && <Badge variant="info">Primary</Badge>}
                  <Badge variant={domain.verified ? "low" : "default"}>
                    {domain.verified ? "Verified" : "Unverified"}
                  </Badge>
                </div>
                <div className="flex items-center gap-2">
                  {!domain.is_primary && (
                    <Button size="sm" variant="ghost" onClick={() => makePrimary(domain)}>
                      Set primary
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => removeDomain(domain)}>
                    <Trash2 size={13} /> Remove
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="grid gap-5 border-t border-border-subtle pt-5 xl:grid-cols-2">
        <div className="space-y-4 rounded-md border border-border-subtle bg-bg-elevated p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <KeyRound size={16} className="text-fg-muted" />
              <h3 className="text-sm font-semibold text-fg-primary">OIDC SSO</h3>
            </div>
            <Badge variant={sso?.is_active ? "low" : "default"}>
              {sso?.is_active ? "Active" : "Inactive"}
            </Badge>
          </div>
          <AuthFields
            idPrefix="oidc"
            active={ssoForm.is_active}
            setActive={(is_active) => setSsoForm({ ...ssoForm, is_active })}
            defaultRole={ssoForm.default_role}
            setDefaultRole={(default_role) => setSsoForm({ ...ssoForm, default_role })}
            allowedDomains={ssoForm.allowed_email_domains}
            setAllowedDomains={(allowed_email_domains) =>
              setSsoForm({ ...ssoForm, allowed_email_domains })
            }
          />
          <div>
            <Label htmlFor="oidc-discovery" required>Discovery URL</Label>
            <Input
              id="oidc-discovery"
              value={ssoForm.discovery_url}
              onChange={(e) => setSsoForm({ ...ssoForm, discovery_url: e.target.value })}
              required
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="oidc-client-id" required>Client ID</Label>
              <Input
                id="oidc-client-id"
                value={ssoForm.client_id}
                onChange={(e) => setSsoForm({ ...ssoForm, client_id: e.target.value })}
                required
              />
            </div>
            <div>
              <Label htmlFor="oidc-client-secret">
                Client secret{sso?.has_client_secret ? " (saved)" : ""}
              </Label>
              <Input
                id="oidc-client-secret"
                type="password"
                value={ssoForm.client_secret}
                onChange={(e) => setSsoForm({ ...ssoForm, client_secret: e.target.value })}
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="oidc-scopes">Scopes</Label>
              <Input
                id="oidc-scopes"
                value={ssoForm.scopes}
                onChange={(e) => setSsoForm({ ...ssoForm, scopes: e.target.value })}
              />
            </div>
            <div>
              <Label htmlFor="oidc-email-claim">Email claim</Label>
              <Input
                id="oidc-email-claim"
                value={ssoForm.email_claim}
                onChange={(e) => setSsoForm({ ...ssoForm, email_claim: e.target.value })}
              />
            </div>
            <div>
              <Label htmlFor="oidc-name-claim">Name claim</Label>
              <Input
                id="oidc-name-claim"
                value={ssoForm.name_claim}
                onChange={(e) => setSsoForm({ ...ssoForm, name_claim: e.target.value })}
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={saveSso} loading={savingSso}>
              Save OIDC
            </Button>
            {sso && (
              <Button type="button" variant="ghost" onClick={disableSso}>
                Disable
              </Button>
            )}
          </div>
        </div>

        <div className="space-y-4 rounded-md border border-border-subtle bg-bg-elevated p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ShieldCheck size={16} className="text-fg-muted" />
              <h3 className="text-sm font-semibold text-fg-primary">SAML</h3>
            </div>
            <Badge variant={saml?.is_active ? "low" : "default"}>
              {saml?.is_active ? "Active" : "Inactive"}
            </Badge>
          </div>
          <AuthFields
            idPrefix="saml"
            active={samlForm.is_active}
            setActive={(is_active) => setSamlForm({ ...samlForm, is_active })}
            defaultRole={samlForm.default_role}
            setDefaultRole={(default_role) => setSamlForm({ ...samlForm, default_role })}
            allowedDomains={samlForm.allowed_email_domains}
            setAllowedDomains={(allowed_email_domains) =>
              setSamlForm({ ...samlForm, allowed_email_domains })
            }
          />
          <div>
            <Label htmlFor="saml-metadata-mode">Metadata source</Label>
            <Select
              id="saml-metadata-mode"
              value={samlForm.metadataMode}
              onChange={(e) =>
                setSamlForm({ ...samlForm, metadataMode: e.target.value as "url" | "xml" })
              }
            >
              <option value="url">Metadata URL</option>
              <option value="xml">Raw XML</option>
            </Select>
          </div>
          {samlForm.metadataMode === "url" ? (
            <div>
              <Label htmlFor="saml-metadata-url" required>Metadata URL</Label>
              <Input
                id="saml-metadata-url"
                value={samlForm.idp_metadata_url}
                onChange={(e) => setSamlForm({ ...samlForm, idp_metadata_url: e.target.value })}
                required
              />
            </div>
          ) : (
            <div>
              <Label htmlFor="saml-metadata-xml" required>Raw metadata XML</Label>
              <Textarea
                id="saml-metadata-xml"
                rows={5}
                value={samlForm.idp_metadata_xml}
                placeholder={saml?.has_idp_metadata_xml ? "Raw XML is already saved. Paste new XML to replace it." : ""}
                onChange={(e) => setSamlForm({ ...samlForm, idp_metadata_xml: e.target.value })}
                required={!saml?.has_idp_metadata_xml}
              />
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="saml-email-attribute">Email attribute</Label>
              <Input
                id="saml-email-attribute"
                value={samlForm.email_attribute}
                onChange={(e) => setSamlForm({ ...samlForm, email_attribute: e.target.value })}
              />
            </div>
            <div>
              <Label htmlFor="saml-name-attribute">Name attribute</Label>
              <Input
                id="saml-name-attribute"
                value={samlForm.name_attribute}
                onChange={(e) => setSamlForm({ ...samlForm, name_attribute: e.target.value })}
              />
            </div>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="flex items-center gap-2 text-sm text-fg-secondary">
              <input
                type="checkbox"
                checked={samlForm.want_assertions_signed}
                onChange={(e) =>
                  setSamlForm({ ...samlForm, want_assertions_signed: e.target.checked })
                }
              />
              Signed assertions
            </label>
            <label className="flex items-center gap-2 text-sm text-fg-secondary">
              <input
                type="checkbox"
                checked={samlForm.want_response_signed}
                onChange={(e) =>
                  setSamlForm({ ...samlForm, want_response_signed: e.target.checked })
                }
              />
              Signed responses
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={saveSaml} loading={savingSaml}>
              Save SAML
            </Button>
            {saml && (
              <Button type="button" variant="ghost" onClick={disableSaml}>
                Disable
              </Button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function AuthFields({
  idPrefix,
  active,
  setActive,
  defaultRole,
  setDefaultRole,
  allowedDomains,
  setAllowedDomains,
}: {
  idPrefix: string;
  active: boolean;
  setActive: (value: boolean) => void;
  defaultRole: Role;
  setDefaultRole: (value: Role) => void;
  allowedDomains: string;
  setAllowedDomains: (value: string) => void;
}) {
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="mt-6 flex items-center gap-2 text-sm text-fg-secondary">
          <input
            type="checkbox"
            checked={active}
            onChange={(e) => setActive(e.target.checked)}
          />
          Active
        </label>
        <div>
          <Label htmlFor={`${idPrefix}-default-role`}>Default role</Label>
          <Select
            id={`${idPrefix}-default-role`}
            value={defaultRole}
            onChange={(e) => setDefaultRole(e.target.value as Role)}
          >
            <option value="admin">Admin</option>
            <option value="operator">Operator</option>
            <option value="viewer">Viewer</option>
          </Select>
        </div>
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-allowed-domains`}>Allowed email domains</Label>
        <Input
          id={`${idPrefix}-allowed-domains`}
          value={allowedDomains}
          placeholder="example.com, ops.example.com"
          onChange={(e) => setAllowedDomains(e.target.value)}
        />
      </div>
    </>
  );
}
