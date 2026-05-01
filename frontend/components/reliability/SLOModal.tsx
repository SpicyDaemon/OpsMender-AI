"use client";

import { useEffect, useState } from "react";
import { createSLO, updateSLO } from "@/lib/api_reliability";
import type { SLOResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, FormError } from "@/components/ui/Input";
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
    objective_pct: 99.9,
    window_seconds: 30 * 24 * 60 * 60,
    burn_alert_threshold: "" as string | number,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      if (initialData) {
        setForm({
          name: initialData.name,
          objective_pct: initialData.objective_pct,
          window_seconds: initialData.window_seconds,
          burn_alert_threshold: initialData.burn_alert_threshold ?? "",
        });
      } else {
        setForm({
          name: "",
          objective_pct: 99.9,
          window_seconds: 30 * 24 * 60 * 60, // 30d
          burn_alert_threshold: "",
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
        objective_pct: Number(form.objective_pct),
        window_seconds: Number(form.window_seconds),
        burn_alert_threshold: form.burn_alert_threshold ? Number(form.burn_alert_threshold) : null,
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
    <Modal
      open={open}
      onClose={onClose}
      title={initialData ? "Edit SLO" : "Create SLO"}
      maxWidth="max-w-md"
    >
      <div className="space-y-4">
        <div>
          <Label htmlFor="slo-name">SLO Name</Label>
          <Input
            id="slo-name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="API Availability"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor="slo-obj">Objective (%)</Label>
            <Input
              id="slo-obj"
              type="number"
              step="0.01"
              max="100"
              min="0"
              value={form.objective_pct}
              onChange={(e) => setForm({ ...form, objective_pct: Number(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="slo-window">Time Window</Label>
            <Select
              id="slo-window"
              value={form.window_seconds}
              onChange={(e) => setForm({ ...form, window_seconds: Number(e.target.value) })}
            >
              <option value={7 * 24 * 60 * 60}>7 Days</option>
              <option value={30 * 24 * 60 * 60}>30 Days</option>
              <option value={90 * 24 * 60 * 60}>90 Days</option>
              <option value={365 * 24 * 60 * 60}>1 Year</option>
            </Select>
          </div>
        </div>

        <div>
          <Label htmlFor="slo-burn">Burn Rate Alert Threshold (Optional)</Label>
          <Input
            id="slo-burn"
            type="number"
            step="0.1"
            min="1"
            value={form.burn_alert_threshold}
            onChange={(e) => setForm({ ...form, burn_alert_threshold: e.target.value })}
            placeholder="e.g. 5 (times normal burn rate)"
          />
          <p className="mt-1 text-xs text-fg-muted">
            If set, a burn rate above this multiplier will trigger an alert.
          </p>
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            {initialData ? "Save Changes" : "Create SLO"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
