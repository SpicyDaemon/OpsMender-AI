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
type SsoMethod = "disabled" | "oidc" | "saml";

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
  const [removingSso, setRemovingSso] = useState(false);

  const [orgName, setOrgName] = useState("");
  const [ssoMethod, setSsoMethod] = useState<SsoMethod>("disabled");
  const [newDomain, setNewDomain] = useState("");
  const [ssoForm, setSsoForm] = useState({
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
    setNotice("");
    try {
      const [orgRes, domainRes, ssoRes, samlRes] = await Promise.all([
        getOrganization(orgId),
        listOrganizationDomains(orgId),
        getOrgSSOConfig(orgId).catch(() => null),
        getOrgSAMLConfig(orgId).catch(() => null),
      ]);
      const activeSso = ssoRes?.configured ? ssoRes : null;
      const activeSaml = samlRes?.configured ? samlRes : null;
      setOrg(orgRes);
      setOrgName(orgRes.name);
      setDomains(domainRes.items);
      setSso(activeSso);
      setSaml(activeSaml);
      setSsoMethod(
        activeSso?.is_active
          ? "oidc"
          : activeSaml?.is_active
            ? "saml"
            : activeSso
              ? "oidc"
              : activeSaml
                ? "saml"
                : "disabled",
      );
      if (activeSso) {
        setSsoForm({
          discovery_url: activeSso.discovery_url,
          client_id: activeSso.client_id,
          client_secret: "",
          scopes: activeSso.scopes,
          email_claim: activeSso.email_claim,
          name_claim: activeSso.name_claim,
          default_role: activeSso.default_role,
          allowed_email_domains: activeSso.allowed_email_domains ?? "",
        });
      }
      if (activeSaml) {
        setSamlForm({
          metadataMode: activeSaml.idp_metadata_url ? "url" : "xml",
          idp_metadata_url: activeSaml.idp_metadata_url ?? "",
          idp_metadata_xml: "",
          email_attribute: activeSaml.email_attribute,
          name_attribute: activeSaml.name_attribute,
          default_role: activeSaml.default_role,
          allowed_email_domains: activeSaml.allowed_email_domains ?? "",
          want_assertions_signed: activeSaml.want_assertions_signed,
          want_response_signed: activeSaml.want_response_signed,
        });
      }
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Failed to load organization settings.");
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveOrg() {
    if (!orgName.trim()) {
      setNotice("Organization name is required.");
      return;
    }
    setSavingOrg(true);
    setNotice("");
    try {
      const saved = await updateOrganization(orgId, { name: orgName.trim() });
      setOrg(saved);
      setOrgName(saved.name);
      // Let the top bar refresh its org-name badge immediately.
      window.dispatchEvent(new CustomEvent("opsmender:org-updated"));
      toast.success("Organization saved");
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

  // Activate OIDC. One SSO method at a time, so any SAML config is removed.
  async function saveOidc() {
    setSavingSso(true);
    setNotice("");
    try {
      const saved = await upsertOrgSSOConfig(orgId, {
        provider: "oidc",
        is_active: true,
        discovery_url: ssoForm.discovery_url.trim(),
        client_id: ssoForm.client_id.trim(),
        client_secret: ssoForm.client_secret.trim() || undefined,
        scopes: ssoForm.scopes.trim() || "openid email profile",
        email_claim: ssoForm.email_claim.trim() || "email",
        name_claim: ssoForm.name_claim.trim() || "name",
        default_role: ssoForm.default_role,
        allowed_email_domains: ssoForm.allowed_email_domains.trim() || null,
      });
      if (saml) {
        await deleteOrgSAMLConfig(orgId).catch(() => {});
        setSaml(null);
      }
      setSso(saved);
      setSsoForm((form) => ({ ...form, client_secret: "" }));
      setSsoMethod("oidc");
      toast.success("OIDC sign-in enabled");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "OIDC save failed.");
    } finally {
      setSavingSso(false);
    }
  }

  // Activate SAML. One SSO method at a time, so any OIDC config is removed.
  async function saveSaml() {
    setSavingSaml(true);
    setNotice("");
    try {
      const saved = await upsertOrgSAMLConfig(orgId, {
        is_active: true,
        idp_metadata_url:
          samlForm.metadataMode === "url" ? samlForm.idp_metadata_url.trim() : null,
        idp_metadata_xml:
          samlForm.metadataMode === "xml" ? samlForm.idp_metadata_xml.trim() : null,
        email_attribute: samlForm.email_attribute.trim() || "email",
        name_attribute: samlForm.name_attribute.trim() || "name",
        default_role: samlForm.default_role,
        allowed_email_domains: samlForm.allowed_email_domains.trim() || null,
        want_assertions_signed: samlForm.want_assertions_signed,
        want_response_signed: samlForm.want_response_signed,
      });
      if (sso) {
        await deleteOrgSSOConfig(orgId).catch(() => {});
        setSso(null);
      }
      setSaml(saved);
      setSamlForm((form) => ({
        ...form,
        idp_metadata_xml: "",
        metadataMode: saved.idp_metadata_url ? "url" : "xml",
      }));
      setSsoMethod("saml");
      toast.success("SAML sign-in enabled");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "SAML save failed.");
    } finally {
      setSavingSaml(false);
    }
  }

  // Turn SSO off entirely — remove whichever config exists.
  async function disableSso() {
    if (!confirm("Disable single sign-on? Members will sign in with email + password.")) {
      setSsoMethod(sso ? "oidc" : saml ? "saml" : "disabled");
      return;
    }
    setRemovingSso(true);
    setNotice("");
    try {
      if (sso) await deleteOrgSSOConfig(orgId).catch(() => {});
      if (saml) await deleteOrgSAMLConfig(orgId).catch(() => {});
      setSso(null);
      setSaml(null);
      setSsoMethod("disabled");
      toast.success("Single sign-on disabled");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not disable SSO.");
    } finally {
      setRemovingSso(false);
    }
  }

  function onMethodChange(method: SsoMethod) {
    if (method === "disabled" && (sso || saml)) {
      void disableSso();
      return;
    }
    setSsoMethod(method);
  }

  if (loading) {
    return (
      <section className="rounded-xl border border-border-subtle bg-bg-panel p-5 text-sm text-fg-muted">
        Loading organization settings…
      </section>
    );
  }

  return (
    <section id="organization-auth" className="space-y-5 rounded-xl border border-border-subtle bg-bg-panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-fg-primary">Organization</h2>
          <p className="mt-1 text-sm text-fg-secondary">
            The single organization for this instance. Its name appears across the
            app and on incident communications.
          </p>
        </div>
        {org && <Badge variant="default">{org.slug}</Badge>}
      </div>

      <FormAlert message={notice} />

      <div className="max-w-md">
        <Label htmlFor="org-name" required>Name</Label>
        <Input
          id="org-name"
          value={orgName}
          onChange={(e) => setOrgName(e.target.value)}
          required
        />
        <p className="mt-1 text-xs text-fg-muted">
          Shown in the top bar and on paging / chat messages.
        </p>
      </div>
      <div className="flex justify-end">
        <Button onClick={saveOrg} loading={savingOrg}>
          Save organization
        </Button>
      </div>

      <div className="border-t border-border-subtle pt-5">
        <div className="mb-1 flex items-center gap-2">
          <Globe2 size={16} className="text-fg-muted" />
          <h3 className="text-sm font-semibold text-fg-primary">Custom domains</h3>
        </div>
        <p className="mb-3 text-xs text-fg-muted">
          Optional. Serve this instance on your own hostname (e.g. for SSO branding).
        </p>
        <div className="flex flex-col gap-2 sm:flex-row sm:max-w-xl">
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

      <div className="border-t border-border-subtle pt-5">
        <div className="mb-1 flex items-center gap-2">
          <KeyRound size={16} className="text-fg-muted" />
          <h3 className="text-sm font-semibold text-fg-primary">Single sign-on (SSO)</h3>
        </div>
        <p className="mb-3 text-xs text-fg-muted">
          Optional. Let members sign in through your identity provider. Pick one
          method — OpenID Connect (OIDC) or SAML.
        </p>

        <div className="max-w-xs">
          <Label htmlFor="sso-method">Method</Label>
          <Select
            id="sso-method"
            value={ssoMethod}
            onChange={(e) => onMethodChange(e.target.value as SsoMethod)}
            disabled={removingSso}
          >
            <option value="disabled">Disabled (email + password)</option>
            <option value="oidc">OpenID Connect (OIDC)</option>
            <option value="saml">SAML</option>
          </Select>
        </div>

        {ssoMethod === "oidc" && (
          <div className="mt-4 space-y-4 rounded-md border border-border-subtle bg-bg-elevated p-4">
            <div className="flex items-center justify-between gap-3">
              <h4 className="text-sm font-semibold text-fg-primary">OpenID Connect</h4>
              <Badge variant={sso?.is_active ? "low" : "default"}>
                {sso?.is_active ? "Active" : "Not saved"}
              </Badge>
            </div>
            <AuthFields
              idPrefix="oidc"
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
                placeholder="https://idp.example.com/.well-known/openid-configuration"
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
            <Button onClick={saveOidc} loading={savingSso}>
              Save &amp; enable OIDC
            </Button>
          </div>
        )}

        {ssoMethod === "saml" && (
          <div className="mt-4 space-y-4 rounded-md border border-border-subtle bg-bg-elevated p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <ShieldCheck size={16} className="text-fg-muted" />
                <h4 className="text-sm font-semibold text-fg-primary">SAML</h4>
              </div>
              <Badge variant={saml?.is_active ? "low" : "default"}>
                {saml?.is_active ? "Active" : "Not saved"}
              </Badge>
            </div>
            <AuthFields
              idPrefix="saml"
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
            <Button onClick={saveSaml} loading={savingSaml}>
              Save &amp; enable SAML
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}

function AuthFields({
  idPrefix,
  defaultRole,
  setDefaultRole,
  allowedDomains,
  setAllowedDomains,
}: {
  idPrefix: string;
  defaultRole: Role;
  setDefaultRole: (value: Role) => void;
  allowedDomains: string;
  setAllowedDomains: (value: string) => void;
}) {
  return (
    <>
      <div>
        <Label htmlFor={`${idPrefix}-default-role`}>Default role for new members</Label>
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
