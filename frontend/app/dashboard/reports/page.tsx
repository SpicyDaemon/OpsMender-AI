"use client";

import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import {
  createReportSchedule,
  deleteReportSchedule,
  downloadIncidentReport,
  listReportSchedules,
  updateReportSchedule,
} from "@/lib/api";
import type { ReportScheduleResponse } from "@/lib/types";
import { formatDateTime } from "@/lib/formatDate";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Input";

function isoLocal(date: Date): string {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export default function ReportsPage() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const now = new Date();
  const [from, setFrom] = useState(isoLocal(new Date(now.getTime() - 30 * 86400000)));
  const [to, setTo] = useState(isoLocal(now));
  const [schedules, setSchedules] = useState<ReportScheduleResponse[]>([]);
  const [name, setName] = useState("Monthly incident report");
  const [recipients, setRecipients] = useState("");
  const [cadence, setCadence] = useState<"weekly" | "monthly" | "quarterly">("monthly");
  const [format, setFormat] = useState<"csv" | "pdf">("pdf");
  const [notice, setNotice] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);

  function setRange(days: number) {
    const end = new Date();
    setFrom(isoLocal(new Date(end.getTime() - days * 86400000)));
    setTo(isoLocal(end));
  }

  async function reload() {
    if (!admin) return;
    setSchedules((await listReportSchedules()).items);
  }
  useEffect(() => { reload().catch(() => {}); }, [admin]);

  async function download(kind: "csv" | "pdf") {
    const blob = await downloadIncidentReport(kind, new Date(from).toISOString(), new Date(to).toISOString());
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `opsmender-incidents.${kind}`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function saveSchedule() {
    const emails = recipients.split(",").map((item) => item.trim()).filter(Boolean);
    if (!emails.length) return;
    const payload = {
      name,
      cadence,
      recipients: emails,
      format,
      next_run_at: new Date(to).toISOString(),
      filters: {},
      enabled: true,
    } as const;
    if (editingId) {
      await updateReportSchedule(editingId, payload);
    } else {
      await createReportSchedule(payload);
    }
    setNotice(editingId ? "Report schedule updated." : "Report schedule created.");
    setEditingId(null);
    await reload();
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-fg-primary">Incident Reports</h1>
        <p className="mt-1 text-sm text-fg-secondary">Inform stakeholders with on-demand CSV/PDF exports or scheduled email reports.</p>
      </div>
      <section className="space-y-4 rounded-xl border border-border-subtle bg-bg-panel p-5">
        <h2 className="font-semibold text-fg-primary">On-demand export</h2>
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end">
          <div>
            <Label htmlFor="report-from">From</Label>
            <Input
              id="report-from"
              type="datetime-local"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="report-to">To</Label>
            <Input
              id="report-to"
              type="datetime-local"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => setRange(7)}>
              7d
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setRange(30)}>
              30d
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => download("csv")}>Download CSV</Button>
          <Button variant="secondary" onClick={() => download("pdf")}>Download PDF</Button>
        </div>
      </section>
      {admin && (
        <section className="space-y-4 rounded-xl border border-border-subtle bg-bg-panel p-5">
          <h2 className="font-semibold text-fg-primary">Scheduled reports</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div><Label htmlFor="schedule-name">Name</Label><Input id="schedule-name" value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div><Label htmlFor="schedule-recipients">Recipients</Label><Input id="schedule-recipients" placeholder="ops@example.com, leaders@example.com" value={recipients} onChange={(e) => setRecipients(e.target.value)} /></div>
            <div><Label htmlFor="schedule-cadence">Cadence</Label><Select id="schedule-cadence" value={cadence} onChange={(e) => setCadence(e.target.value as typeof cadence)}><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option></Select></div>
            <div><Label htmlFor="schedule-format">Format</Label><Select id="schedule-format" value={format} onChange={(e) => setFormat(e.target.value as typeof format)}><option value="pdf">PDF</option><option value="csv">CSV</option></Select></div>
          </div>
          <Button onClick={saveSchedule} disabled={!name || !recipients}>{editingId ? "Save schedule" : "Create schedule"}</Button>
          {notice && <p className="text-sm text-fg-secondary">{notice}</p>}
          <div className="divide-y divide-border-subtle">
            {schedules.map((schedule) => (
              <div key={schedule.id} className="flex items-center justify-between gap-4 py-3">
                <div><p className="font-medium text-fg-primary">{schedule.name}</p><p className="text-xs text-fg-muted">{schedule.cadence} · {schedule.format.toUpperCase()} · next {formatDateTime(schedule.next_run_at)}</p>{schedule.last_error && <p className="text-xs text-status-critical">{schedule.last_error}</p>}</div>
                <div className="flex flex-nowrap gap-2">
                  <Button variant="secondary" size="sm" onClick={() => {
                    setEditingId(schedule.id);
                    setName(schedule.name);
                    setRecipients(schedule.recipients.join(", "));
                    setCadence(schedule.cadence);
                    setFormat(schedule.format);
                    setTo(isoLocal(new Date(schedule.next_run_at)));
                  }}>Edit</Button>
                  <Button variant="secondary" size="sm" onClick={async () => {
                    await updateReportSchedule(schedule.id, {
                      name: schedule.name,
                      cadence: schedule.cadence,
                      recipients: schedule.recipients,
                      filters: schedule.filters,
                      format: schedule.format,
                      next_run_at: schedule.next_run_at,
                      enabled: !schedule.enabled,
                    });
                    await reload();
                  }}>{schedule.enabled ? "Disable" : "Enable"}</Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-status-critical hover:bg-status-critical-bg hover:text-status-critical"
                    aria-label={`Delete report schedule ${schedule.name}`}
                    title={`Delete report schedule ${schedule.name}`}
                    onClick={async () => {
                      if (!window.confirm(`Delete report schedule "${schedule.name}"?`)) return;
                      await deleteReportSchedule(schedule.id);
                      await reload();
                    }}
                  >
                    <Trash2 size={13} />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
