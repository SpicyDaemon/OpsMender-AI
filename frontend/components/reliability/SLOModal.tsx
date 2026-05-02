"use client";

import { useEffect, useState } from "react";
import { createSLO, updateSLO } from "@/lib/api_reliability";
import type { SLOResponse, SLATargetResponse } from "@/lib/types";
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

export function SLOModal({ open, onClose, onSaved, targetId, initialData }: SLOModalProps) {
  const [form, setForm] = useState({
    name: "",
    objective_pct: "99.9",
    window_hours: "720", // 30 days
    burn_alert_threshold: "1.0",
    alerts_enabled: true,
    is_active: true,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      if (initialData) {
        setForm({
          name: initialData.name,
          objective_pct: String(initialData.objective_pct),
          window_hours: String(initialData.window_seconds / 3600),
          burn_alert_threshold: initialData.burn_alert_threshold ? String(initialData.burn_alert_threshold) : "1.0",
          alerts_enabled: initialData.burn_alert_threshold !== null,
          is_active: initialData.is_active,
        });
      } else {
        setForm({
          name: "",
          objective_pct: "99.9",
          window_hours: "720",
          burn_alert_threshold: "1.0",
          alerts_enabled: true,
          is_active: true,
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
      const payload = {
        target_id: targetId,
        name: form.name.trim(),
        objective_pct: parseFloat(form.objective_pct),
        window_seconds: parseInt(form.window_hours, 10) * 3600,
        burn_alert_threshold: form.alerts_enabled ? parseFloat(form.burn_alert_threshold) : null,
        is_active: form.is_active,
      };

      if (isNaN(payload.objective_pct) || payload.objective_pct < 0 || payload.objective_pct > 100) {
        setError("Objective must be between 0 and 100%");
        setSaving(false);
        return;
      }

      if (isNaN(payload.window_seconds) || payload.window_seconds < 3600) {
        setError("Window must be at least 1 hour");
        setSaving(false);
        return;
      }

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
    <Modal
      open={open}
      onClose={onClose}
      title={initialData ? "Edit SLO" : "Add SLO"}
      maxWidth="max-w-md"
    >
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
            <Label htmlFor="slo-objective">Objective (%)</Label>
            <Input
              id="slo-objective"
              type="number"
              step="0.01"
              value={form.objective_pct}
              onChange={(e) => setForm({ ...form, objective_pct: e.target.value })}
              placeholder="99.9"
            />
          </div>
          <div>
            <Label htmlFor="slo-window">Window (Hours)</Label>
            <Input
              id="slo-window"
              type="number"
              value={form.window_hours}
              onChange={(e) => setForm({ ...form, window_hours: e.target.value })}
              placeholder="720"
            />
            <p className="mt-1 text-[10px] text-fg-muted">720h = 30 days</p>
          </div>
        </div>

        <div className="p-4 rounded-xl border border-border-subtle bg-bg-elevated space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h4 className="text-xs font-semibold text-fg-primary uppercase tracking-wider">Burn Rate Alerting</h4>
              <p className="text-[10px] text-fg-secondary">Auto-generate incidents on rapid budget burn.</p>
            </div>
            <Toggle
              checked={form.alerts_enabled}
              onChange={(checked) => setForm({ ...form, alerts_enabled: checked })}
            />
          </div>

          {form.alerts_enabled && (
            <div className="pt-2 border-t border-border-subtle">
              <Label htmlFor="slo-threshold">Burn Threshold (x)</Label>
              <Input
                id="slo-threshold"
                type="number"
                step="0.1"
                value={form.burn_alert_threshold}
                onChange={(e) => setForm({ ...form, burn_alert_threshold: e.target.value })}
                placeholder="1.0"
              />
              <p className="mt-1 text-[10px] text-fg-muted">
                1.0x means alerting if the error budget will be exhausted exactly by the end of the window.
              </p>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between py-2">
          <Label htmlFor="slo-active" className="mb-0">SLO Enabled</Label>
          <Toggle
            id="slo-active"
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
            {initialData ? "Save Changes" : "Add SLO"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
