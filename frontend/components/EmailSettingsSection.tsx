"use client";

import { useEffect, useState } from "react";
import {
  getOrgEmailSettings,
  testOrgEmailSettings,
  updateOrgEmailSettings,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Input";

export function EmailSettingsSection({ orgId }: { orgId: string }) {
  const [form, setForm] = useState({
    host: "",
    port: 587,
    security: "starttls" as "starttls" | "ssl" | "none",
    username: "",
    password: "",
    from_name: "OpsMender",
    from_address: "",
  });
  const [hasPassword, setHasPassword] = useState(false);
  const [recipient, setRecipient] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  // Status of the *active* (resolved) SMTP config + last test result.
  const [configured, setConfigured] = useState(false);
  const [source, setSource] = useState<"database" | "environment" | null>(null);
  const [activeHost, setActiveHost] = useState("");
  const [testState, setTestState] = useState<"idle" | "ok" | "fail">("idle");
  const [testing, setTesting] = useState(false);

  function applySettings(settings: {
    configured: boolean;
    host: string;
    port: number;
    security: "starttls" | "ssl" | "none";
    username: string | null;
    from_name: string | null;
    from_address: string;
    has_password: boolean;
    source: "database" | "environment" | null;
  }) {
    setForm({
      host: settings.host,
      port: settings.port,
      security: settings.security,
      username: settings.username ?? "",
      password: "",
      from_name: settings.from_name ?? "OpsMender",
      from_address: settings.from_address,
    });
    setHasPassword(settings.has_password);
    setConfigured(settings.configured);
    setSource(settings.source);
    setActiveHost(settings.configured ? settings.host : "");
  }

  useEffect(() => {
    getOrgEmailSettings(orgId)
      .then(applySettings)
      .catch(() => {
        setConfigured(false);
        setSource(null);
      });
  }, [orgId]);

  async function save() {
    setSaving(true);
    setNotice("");
    try {
      const saved = await updateOrgEmailSettings(orgId, {
        ...form,
        username: form.username || null,
        password: form.password || undefined,
        from_name: form.from_name || null,
      });
      applySettings(saved);
      setTestState("idle"); // config changed — prior test result no longer valid
      setNotice("SMTP settings saved.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function sendTest() {
    if (!recipient) return;
    setTesting(true);
    setNotice("");
    try {
      const result = await testOrgEmailSettings(orgId, recipient);
      setTestState(result.success ? "ok" : "fail");
      setNotice(result.detail);
    } catch (error) {
      setTestState("fail");
      setNotice(error instanceof Error ? error.message : "Test failed.");
    } finally {
      setTesting(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border border-border-subtle bg-bg-panel p-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold text-fg-primary">Email / SMTP</h2>
          {configured ? (
            <span className="inline-flex items-center gap-1 rounded-pill border border-status-low-border bg-status-low-bg px-2 py-0.5 text-[11px] font-medium text-status-low">
              Configured · {source === "database" ? "saved here" : "from environment"}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-pill border border-border-subtle bg-bg-elevated px-2 py-0.5 text-[11px] font-medium text-fg-muted">
              Not configured
            </span>
          )}
          {testState === "ok" && (
            <span className="inline-flex items-center gap-1 rounded-pill border border-status-low-border bg-status-low-bg px-2 py-0.5 text-[11px] font-medium text-status-low">
              ✓ Test passed
            </span>
          )}
          {testState === "fail" && (
            <span className="inline-flex items-center gap-1 rounded-pill border border-status-critical-border bg-status-critical-bg px-2 py-0.5 text-[11px] font-medium text-status-critical">
              ✕ Test failed
            </span>
          )}
        </div>
        <p className="mt-1 text-sm text-fg-secondary">
          Highly recommended. The single SMTP setting for this workspace — powers
          invitations, password resets, default user email, scheduled incident
          reports, and email/voice paging.
          {configured && activeHost ? (
            <>
              {" "}Active server: <span className="font-medium text-fg-primary">{activeHost}:{form.port}</span>.
            </>
          ) : null}
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <div className="md:col-span-2">
          <Label htmlFor="smtp-host">Host</Label>
          <Input id="smtp-host" value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} />
        </div>
        <div>
          <Label htmlFor="smtp-port">Port</Label>
          <Input id="smtp-port" type="number" value={form.port} onChange={(e) => setForm({ ...form, port: Number(e.target.value) })} />
        </div>
        <div>
          <Label htmlFor="smtp-security">Security</Label>
          <Select id="smtp-security" value={form.security} onChange={(e) => setForm({ ...form, security: e.target.value as typeof form.security })}>
            <option value="starttls">STARTTLS</option>
            <option value="ssl">SSL/TLS</option>
            <option value="none">None</option>
          </Select>
        </div>
        <div>
          <Label htmlFor="smtp-user">Username</Label>
          <Input id="smtp-user" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        </div>
        <div>
          <Label htmlFor="smtp-password">Password</Label>
          <Input id="smtp-password" type="password" value={form.password} placeholder={hasPassword ? "Saved — leave blank to keep" : ""} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </div>
        <div>
          <Label htmlFor="smtp-from-name">From name</Label>
          <Input id="smtp-from-name" value={form.from_name} onChange={(e) => setForm({ ...form, from_name: e.target.value })} />
        </div>
        <div className="md:col-span-2">
          <Label htmlFor="smtp-from-address">From address</Label>
          <Input id="smtp-from-address" type="email" value={form.from_address} onChange={(e) => setForm({ ...form, from_address: e.target.value })} />
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Button onClick={save} loading={saving} disabled={!form.host || !form.from_address}>Save SMTP</Button>
        <div className="min-w-64 flex-1">
          <Label htmlFor="smtp-test-recipient">Test recipient</Label>
          <Input id="smtp-test-recipient" type="email" value={recipient} onChange={(e) => setRecipient(e.target.value)} />
        </div>
        <Button variant="secondary" onClick={sendTest} loading={testing} disabled={!recipient}>Test now</Button>
      </div>
      {notice && (
        <p
          className={`text-sm ${
            testState === "fail" ? "text-status-critical" : "text-fg-secondary"
          }`}
        >
          {notice}
        </p>
      )}
    </section>
  );
}
