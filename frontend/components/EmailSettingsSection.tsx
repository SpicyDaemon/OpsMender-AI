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

  useEffect(() => {
    getOrgEmailSettings(orgId)
      .then((settings) => {
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
      })
      .catch(() => {});
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
      setHasPassword(saved.has_password);
      setForm((current) => ({ ...current, password: "" }));
      setNotice("SMTP settings saved.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function sendTest() {
    if (!recipient) return;
    const result = await testOrgEmailSettings(orgId, recipient);
    setNotice(result.detail);
  }

  return (
    <section className="space-y-4 rounded-xl border border-border-subtle bg-bg-panel p-5">
      <div>
        <h2 className="text-base font-semibold text-fg-primary">Email / SMTP</h2>
        <p className="mt-1 text-sm text-fg-secondary">
          Highly recommended. Powers invitations, password resets, default user email,
          and scheduled incident reports.
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
        <Button variant="secondary" onClick={sendTest} disabled={!recipient}>Send test</Button>
      </div>
      {notice && <p className="text-sm text-fg-secondary">{notice}</p>}
    </section>
  );
}
