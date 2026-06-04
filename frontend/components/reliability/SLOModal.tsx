"use client";

import { useEffect, useState } from "react";
import { createSLO, updateSLO } from "@/lib/api_reliability";
import type { SLOResponse } from "@/lib/types";
import { SLO_WINDOW_OPTIONS } from "@/lib/uptime";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, FormError } from "@/components/ui/Input";
import { Toggle } from "@/components/ui/Toggle";
import { Modal } from "@/components/ui/Modal";

interface SLOModalProps {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  targetId: string;
  initialData?: SLOResponse | null;
}

const PRESET_VALUES = SLO_WINDOW_OPTIONS.map((o) => o.value);

/**
 * v1 SLO editor — name, target %, window, enabled. Burn-rate alerting and
 * error-budget math are intentionally not exposed here (too SRE-heavy for v1);
 * the backend fields remain and are simply left unset. SLO breaches show as a
 * warning on the Reliability dashboard and never create incidents in v1.
 */
export function SLOModal({ open, onClose, onSaved, targetId, initialData }: SLOModalProps) {
  const [form, setForm] = useState({
    name: "",
    objective_pct: "99.9",
    window_seconds: 30 * 86400,
    is_active: true,
  });
  const [customWindow, setCustomWindow] = useState(false);
  const [customDays, setCustomDays] = useState("30");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    if (initialData) {
      const isPreset = PRESET_VALUES.includes(initialData.window_seconds);
      setForm({
        name: initialData.name,
        objective_pct: String(initialData.objective_pct),
        window_seconds: initialData.window_seconds,
        is_active: initialData.is_active,
      });
      setCustomWindow(!isPreset);
      setCustomDays(String(Math.round(initialData.window_seconds / 86400)));
    } else {
      setForm({ name: "", objective_pct: "99.9", window_seconds: 30 * 86400, is_active: true });
      setCustomWindow(false);
      setCustomDays("30");
    }
    setError("");
  }, [open, initialData]);

  async function handleSubmit() {
    if (!form.name.trim()) {
      setError("Name is required");
      return;
    }
    const objective = parseFloat(form.objective_pct);
    if (Number.isNaN(objective) || objective < 0 || objective > 100) {
      setError("Target must be between 0 and 100%");
      return;
    }
    const windowSeconds = customWindow
      ? Math.round(parseFloat(customDays) * 86400)
      : form.window_seconds;
    if (Number.isNaN(windowSeconds) || windowSeconds < 3600) {
      setError("Window must be at least 1 hour");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const payload = {
        target_id: targetId,
        name: form.name.trim(),
        objective_pct: objective,
        window_seconds: windowSeconds,
        // v1: no burn-rate alerting — breaches are warning-only, never paging.
        burn_alert_threshold: null,
        is_active: form.is_active,
      };
      if (initialData) {
        await updateSLO(initialData.id, payload);
      } else {
        await createSLO(payload);
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save SLO");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={initialData ? "Edit SLO" : "Add SLO"} maxWidth="max-w-md">
      <div className="space-y-4">
        <div>
          <Label htmlFor="slo-name">SLO Name</Label>
          <Input
            id="slo-name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Availability"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor="slo-objective">Target (%)</Label>
            <Input
              id="slo-objective"
              type="number"
              step="0.001"
              min="0"
              max="100"
              value={form.objective_pct}
              onChange={(e) => setForm({ ...form, objective_pct: e.target.value })}
              placeholder="99.9"
            />
            <p className="mt-1 text-[10px] text-fg-muted">Up to 3 decimals, e.g. 99.999</p>
          </div>
          <div>
            <Label htmlFor="slo-window">Window</Label>
            <Select
              id="slo-window"
              value={customWindow ? "custom" : String(form.window_seconds)}
              onChange={(e) => {
                if (e.target.value === "custom") {
                  setCustomWindow(true);
                } else {
                  setCustomWindow(false);
                  setForm({ ...form, window_seconds: Number(e.target.value) });
                }
              }}
            >
              {SLO_WINDOW_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
              <option value="custom">Custom…</option>
            </Select>
            {customWindow && (
              <Input
                aria-label="Custom window in days"
                type="number"
                min="1"
                className="mt-2"
                value={customDays}
                onChange={(e) => setCustomDays(e.target.value)}
                placeholder="Days"
              />
            )}
          </div>
        </div>

        <div className="flex items-center justify-between py-2">
          <Label htmlFor="slo-active" className="mb-0">SLO Enabled</Label>
          <Toggle
            id="slo-active"
            checked={form.is_active}
            onChange={(checked) => setForm({ ...form, is_active: checked })}
          />
        </div>

        <p className="rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2 text-[11px] text-fg-secondary">
          SLO breaches show as a warning on the Reliability dashboard. OpsMender
          does not create incidents from SLO breaches yet.
        </p>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            {initialData ? "Save Changes" : "Add SLO"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
