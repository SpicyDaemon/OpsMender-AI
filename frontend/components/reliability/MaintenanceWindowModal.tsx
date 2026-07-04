"use client";

import { useEffect, useState } from "react";
import { createMaintenanceWindow, updateMaintenanceWindow } from "@/lib/api_reliability";
import type { MaintenanceWindowResponse, SLATargetResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, FormError, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";

interface MaintenanceWindowModalProps {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  targets: SLATargetResponse[];
  initialData?: MaintenanceWindowResponse | null;
}

function formatDateForInput(isoStr: string) {
  const d = new Date(isoStr);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export function MaintenanceWindowModal({ open, onClose, onSaved, targets, initialData }: MaintenanceWindowModalProps) {
  const [form, setForm] = useState({
    name: "",
    reason: "",
    starts_at: "",
    ends_at: "",
    target_id: "*", // Using single target selection for UI simplicity, backend supports array
    rrule: "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      if (initialData) {
        setForm({
          name: initialData.name,
          reason: initialData.reason ?? "",
          starts_at: formatDateForInput(initialData.starts_at),
          ends_at: formatDateForInput(initialData.ends_at),
          target_id: initialData.target_ids[0] ?? "*",
          rrule: initialData.rrule ?? "",
        });
      } else {
        const now = new Date();
        const later = new Date(now.getTime() + 60 * 60 * 1000); // +1 hour
        setForm({
          name: "",
          reason: "",
          starts_at: formatDateForInput(now.toISOString()),
          ends_at: formatDateForInput(later.toISOString()),
          target_id: "*",
          rrule: "",
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
    if (!form.starts_at || !form.ends_at) {
      setError("Start and end times are required");
      return;
    }

    setSaving(true);
    setError("");

    try {
      const payload = {
        name: form.name.trim(),
        reason: form.reason.trim() || null,
        starts_at: new Date(form.starts_at).toISOString(),
        ends_at: new Date(form.ends_at).toISOString(),
        target_ids: [form.target_id], // backend expects an array
        rrule: form.rrule.trim() || null,
      };

      if (initialData) {
        await updateMaintenanceWindow(initialData.id, payload);
      } else {
        await createMaintenanceWindow(payload);
      }

      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save Maintenance Window");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initialData ? "Edit Maintenance Window" : "New Maintenance Window"}
      maxWidth="max-w-md"
    >
      <div className="space-y-4">
        <div>
          <Label htmlFor="mw-name">Name</Label>
          <Input
            id="mw-name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Database Migration"
          />
        </div>

        <div>
          <Label htmlFor="mw-reason">Reason</Label>
          <Textarea
            id="mw-reason"
            value={form.reason}
            onChange={(e) => setForm({ ...form, reason: e.target.value })}
            placeholder="Upgrading primary cluster to v14"
            rows={2}
          />
        </div>

        <div>
          <Label htmlFor="mw-target">Target</Label>
          <Select
            id="mw-target"
            value={form.target_id}
            onChange={(e) => setForm({ ...form, target_id: e.target.value })}
          >
            <option value="*">All Targets</option>
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor="mw-start">Starts At</Label>
            <Input
              id="mw-start"
              type="datetime-local"
              value={form.starts_at}
              onChange={(e) => setForm({ ...form, starts_at: e.target.value })}
            />
          </div>
          <div>
            <Label htmlFor="mw-end">Ends At</Label>
            <Input
              id="mw-end"
              type="datetime-local"
              value={form.ends_at}
              onChange={(e) => setForm({ ...form, ends_at: e.target.value })}
            />
          </div>
        </div>

        <div>
          <Label htmlFor="mw-rrule">Recurrence Rule (Optional)</Label>
          <Input
            id="mw-rrule"
            value={form.rrule}
            onChange={(e) => setForm({ ...form, rrule: e.target.value })}
            placeholder="FREQ=WEEKLY;BYDAY=SU"
          />
          <p className="mt-1 text-xs text-fg-muted">
            Leave blank for a one-off window. Uses iCalendar RRULE format.
          </p>
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            {initialData ? "Save Changes" : "Schedule"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
