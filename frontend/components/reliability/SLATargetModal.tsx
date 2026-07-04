"use client";

import { useEffect, useState } from "react";
import { createSLATarget, updateSLATarget } from "@/lib/api_reliability";
import { listServices } from "@/lib/api";
import type { SLATargetResponse, SLATargetKind, ServiceResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, FormError } from "@/components/ui/Input";
import { Toggle } from "@/components/ui/Toggle";
import { Modal } from "@/components/ui/Modal";

interface SLATargetModalProps {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  initialData?: SLATargetResponse | null;
}

function expectedStatusesText(config: Record<string, unknown>): string {
  const statuses = config.expected_statuses;
  if (Array.isArray(statuses)) {
    return statuses.map((status) => String(status)).join(", ");
  }
  if (typeof statuses === "string") return statuses;
  if (typeof config.expected_status === "number" || typeof config.expected_status === "string") {
    return String(config.expected_status);
  }
  return "200";
}

function parseExpectedStatuses(value: string): Array<number | string> {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => (/^\d+$/.test(part) ? Number(part) : part));
}

export function SLATargetModal({ open, onClose, onSaved, initialData }: SLATargetModalProps) {
  const [form, setForm] = useState({
    name: "",
    kind: "http" as SLATargetKind,
    owner_team: "",
    service_id: "",
    is_active: true,
    // HTTP config
    http_url: "",
    http_method: "GET",
    http_expected_statuses: "200",
    // TCP config
    tcp_host: "",
    tcp_port: "",
  });
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    listServices()
      .then((res) => setServices(res.items))
      .catch(() => setServices([]));
  }, [open]);

  useEffect(() => {
    if (open) {
      if (initialData) {
        const config = initialData.config || {};
        setForm({
          name: initialData.name,
          kind: initialData.kind,
          owner_team: initialData.owner_team || "",
          service_id: initialData.service_id || "",
          is_active: initialData.is_active,
          http_url: initialData.kind === "http" ? (config.url as string || "") : "",
          http_method: initialData.kind === "http" ? (config.method as string || "GET") : "GET",
          http_expected_statuses: initialData.kind === "http" ? expectedStatusesText(config) : "200",
          tcp_host: initialData.kind === "tcp" ? (config.host as string || "") : "",
          tcp_port: initialData.kind === "tcp" ? String(config.port || "") : "",
        });
      } else {
        setForm({
          name: "",
          kind: "http",
          owner_team: "",
          service_id: "",
          is_active: true,
          http_url: "",
          http_method: "GET",
          http_expected_statuses: "200",
          tcp_host: "",
          tcp_port: "",
        });
      }
      setError("");
    }
  }, [open, initialData]);

  async function handleSubmit() {
    if (!form.name.trim()) {
      setError("Name is required");
      return;
    }

    setSaving(true);
    setError("");

    try {
      const config: Record<string, unknown> = {};
      if (form.kind === "http") {
        config.url = form.http_url.trim();
        config.method = form.http_method;
        const expectedStatuses = parseExpectedStatuses(form.http_expected_statuses);
        config.expected_statuses = expectedStatuses;
        
        if (!config.url) {
          setError("URL is required for HTTP targets");
          setSaving(false);
          return;
        }
        if (expectedStatuses.length === 0) {
          setError("At least one expected HTTP status code is required");
          setSaving(false);
          return;
        }
      } else if (form.kind === "tcp") {
        config.host = form.tcp_host.trim();
        config.port = parseInt(form.tcp_port, 10);
        
        if (!config.host || !config.port) {
          setError("Host and Port are required for TCP targets");
          setSaving(false);
          return;
        }
      }

      const payload = {
        name: form.name.trim(),
        kind: form.kind,
        owner_team: form.owner_team.trim() || null,
        service_id: form.service_id || null,
        is_active: form.is_active,
        config: Object.keys(config).length > 0 ? config : null,
      };

      if (initialData) {
        await updateSLATarget(initialData.id, payload);
      } else {
        await createSLATarget(payload);
      }

      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save SLA Target");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initialData ? "Edit SLA Target" : "New SLA Target"}
      maxWidth="max-w-md"
    >
      <div className="space-y-4">
        <div>
          <Label htmlFor="target-name">Name</Label>
          <Input
            id="target-name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Main Website"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor="target-kind">Kind</Label>
            <Select
              id="target-kind"
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value as SLATargetKind })}
            >
              <option value="http">HTTP/HTTPS</option>
              <option value="tcp">TCP Port</option>
              <option value="external">External / Push</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="target-team">Owner Team</Label>
            <Input
              id="target-team"
              value={form.owner_team}
              onChange={(e) => setForm({ ...form, owner_team: e.target.value })}
              placeholder="Platform"
            />
          </div>
        </div>

        <div>
          <Label htmlFor="target-service">Owning Service</Label>
          <Select
            id="target-service"
            value={form.service_id}
            onChange={(e) => setForm({ ...form, service_id: e.target.value })}
          >
            <option value="">— None —</option>
            {services.map((svc) => (
              <option key={svc.id} value={svc.id}>
                {svc.name}
              </option>
            ))}
          </Select>
          <p className="mt-1 text-[11px] text-fg-muted">
            Links breaches to a service so SLO recommendations route to its team.
          </p>
        </div>

        {form.kind === "http" && (
          <div className="space-y-4 p-3 rounded-lg bg-bg-elevated border border-border-subtle">
            <h4 className="text-xs font-semibold text-fg-secondary uppercase tracking-wider">HTTP Configuration</h4>
            <div>
              <Label htmlFor="http-url">URL</Label>
              <Input
                id="http-url"
                value={form.http_url}
                onChange={(e) => setForm({ ...form, http_url: e.target.value })}
                placeholder="https://api.example.com/health"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="http-method">Method</Label>
                <Select
                  id="http-method"
                  value={form.http_method}
                  onChange={(e) => setForm({ ...form, http_method: e.target.value })}
                >
                  <option value="GET">GET</option>
                  <option value="POST">POST</option>
                  <option value="HEAD">HEAD</option>
                </Select>
              </div>
              <div>
                <Label htmlFor="http-status">Expected Status</Label>
                <Input
                  id="http-status"
                  value={form.http_expected_statuses}
                  onChange={(e) => setForm({ ...form, http_expected_statuses: e.target.value })}
                  placeholder="200, 204, 404, 2xx"
                />
                <p className="mt-1 text-[11px] text-fg-muted">
                  Accepts exact codes, classes like 2xx, or ranges like 200-299.
                </p>
              </div>
            </div>
          </div>
        )}

        {form.kind === "tcp" && (
          <div className="space-y-4 p-3 rounded-lg bg-bg-elevated border border-border-subtle">
            <h4 className="text-xs font-semibold text-fg-secondary uppercase tracking-wider">TCP Configuration</h4>
            <div className="grid grid-cols-3 gap-4">
              <div className="col-span-2">
                <Label htmlFor="tcp-host">Host</Label>
                <Input
                  id="tcp-host"
                  value={form.tcp_host}
                  onChange={(e) => setForm({ ...form, tcp_host: e.target.value })}
                  placeholder="db.example.internal"
                />
              </div>
              <div>
                <Label htmlFor="tcp-port">Port</Label>
                <Input
                  id="tcp-port"
                  type="number"
                  value={form.tcp_port}
                  onChange={(e) => setForm({ ...form, tcp_port: e.target.value })}
                  placeholder="5432"
                />
              </div>
            </div>
          </div>
        )}

        {form.kind === "external" && (
          <div className="p-3 rounded-lg bg-bg-elevated border border-border-subtle italic text-xs text-fg-secondary">
            External targets do not poll. They expect uptime heartbeats to be pushed via the API.
          </div>
        )}

        <div className="flex items-center justify-between py-2">
          <Label htmlFor="target-active" className="mb-0">Monitoring Enabled</Label>
          <Toggle
            id="target-active"
            checked={form.is_active}
            onChange={(checked) => setForm({ ...form, is_active: checked })}
          />
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            {initialData ? "Save Changes" : "Create Target"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
