"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  ExternalLink,
  Mail,
  RefreshCw,
  ShieldAlert,
  Wrench,
} from "lucide-react";
import {
  getPublicStatusPage,
  subscribeToPublicStatusPage,
} from "@/lib/api";
import type {
  PublicStatusIncident,
  PublicStatusResponse,
  PublicStatusIncidentUpdate,
  StatusPageComponentStatus,
} from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { FormAlert, Input } from "@/components/ui/Input";
import { formatDateTime } from "@/lib/formatDate";

const STATUS_META: Record<
  StatusPageComponentStatus,
  { label: string; className: string; icon: typeof CheckCircle2 }
> = {
  operational: {
    label: "Operational",
    className: "border-status-low-border bg-status-low-bg text-status-low",
    icon: CheckCircle2,
  },
  maintenance: {
    label: "Maintenance",
    className: "border-status-info-border bg-status-info-bg text-status-info",
    icon: Wrench,
  },
  degraded: {
    label: "Degraded",
    className: "border-status-medium-border bg-status-medium-bg text-status-medium",
    icon: AlertTriangle,
  },
  partial_outage: {
    label: "Partial outage",
    className: "border-status-high-border bg-status-high-bg text-status-high",
    icon: ShieldAlert,
  },
  major_outage: {
    label: "Major outage",
    className: "border-status-critical-border bg-status-critical-bg text-status-critical",
    icon: ShieldAlert,
  },
};

const URL_RE = /(https?:\/\/[^\s<>"']+)/g;

function linkedText(text: string) {
  return text.split(URL_RE).map((part, index) => {
    if (!part.match(URL_RE)) return <span key={`${part}-${index}`}>{part}</span>;
    try {
      const parsed = new URL(part);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        return <span key={`${part}-${index}`}>{part}</span>;
      }
    } catch {
      return <span key={`${part}-${index}`}>{part}</span>;
    }
    return (
      <a
        key={`${part}-${index}`}
        href={part}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-accent-text underline underline-offset-2"
      >
        {part}
        <ExternalLink size={12} />
      </a>
    );
  });
}

function uptimeTone(pct: number) {
  if (pct >= 99.9) return "bg-status-low";
  if (pct >= 99) return "bg-status-medium";
  if (pct >= 95) return "bg-status-high";
  return "bg-status-critical";
}

function IncidentUpdate({ update }: { update: PublicStatusIncidentUpdate }) {
  const meta = STATUS_META[
    update.state === "resolved" ? "operational" : "degraded"
  ];
  return (
    <div className="border-l border-border-subtle pl-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-md border px-2 py-0.5 text-xs ${meta.className}`}>
          {update.state.replaceAll("_", " ")}
        </span>
        <span className="text-xs text-fg-muted">
          {formatDateTime(update.published_at)}
        </span>
      </div>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-fg-secondary">
        {linkedText(update.body)}
      </p>
    </div>
  );
}

function IncidentBlock({
  incident,
  compact = false,
}: {
  incident: PublicStatusIncident;
  compact?: boolean;
}) {
  return (
    <article className="rounded-lg border border-border-subtle bg-bg-panel p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg-primary">{incident.title}</h3>
        {incident.priority && (
          <span className="rounded-md border border-border-subtle bg-bg-elevated px-2 py-0.5 text-xs text-fg-muted">
            {incident.priority}
          </span>
        )}
      </div>
      <div className={`mt-4 space-y-4 ${compact ? "max-h-72 overflow-y-auto pr-1" : ""}`}>
        {incident.updates.map((update, index) => (
          <IncidentUpdate
            key={`${incident.id}-${update.published_at}-${index}`}
            update={update}
          />
        ))}
      </div>
    </article>
  );
}

export function StatusPageView() {
  const [payload, setPayload] = useState<PublicStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [subscribeMessage, setSubscribeMessage] = useState("");
  const [subscribeError, setSubscribeError] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  async function load(refresh = false) {
    if (refresh) setRefreshing(true);
    setError("");
    try {
      setPayload(await getPublicStatusPage());
      setLastUpdated(new Date());
    } catch (err) {
      setPayload(null);
      setError(err instanceof Error ? err.message : "Status page unavailable.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const overall = payload ? STATUS_META[payload.overall_status] : STATUS_META.operational;
  const OverallIcon = overall.icon;

  async function subscribe(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim()) return;
    setSubmitting(true);
    setSubscribeError("");
    setSubscribeMessage("");
    try {
      await subscribeToPublicStatusPage(email.trim());
      setEmail("");
      setSubscribeMessage("Check your inbox to confirm the subscription.");
    } catch (err) {
      setSubscribeError(err instanceof Error ? err.message : "Subscription failed.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <main className="min-h-screen bg-bg-base px-4 py-8 text-fg-primary sm:px-6">
        <div className="mx-auto max-w-5xl space-y-4">
          <div className="h-24 animate-pulse rounded-lg bg-bg-hover" />
          <div className="h-44 animate-pulse rounded-lg bg-bg-hover" />
          <div className="h-44 animate-pulse rounded-lg bg-bg-hover" />
        </div>
      </main>
    );
  }

  if (!payload) {
    return (
      <main className="min-h-screen bg-bg-base px-4 py-8 text-fg-primary sm:px-6">
        <div className="mx-auto max-w-xl rounded-lg border border-border-subtle bg-bg-panel p-6 text-center shadow-sm">
          <ShieldAlert className="mx-auto text-fg-muted" size={28} />
          <h1 className="mt-4 text-lg font-semibold">Status page unavailable</h1>
          <p className="mt-2 text-sm text-fg-secondary">
            {error || "The requested status page is not currently available."}
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-bg-base text-fg-primary">
      <div className="border-b border-border-subtle bg-bg-panel">
        <div className="mx-auto flex max-w-6xl flex-col gap-5 px-4 py-6 sm:px-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-medium ${overall.className}`}>
                <OverallIcon size={15} />
                {overall.label}
              </span>
              <span className="inline-flex items-center gap-1.5 text-xs text-fg-muted">
                <Clock3 size={13} />
                Refreshed {formatDateTime((lastUpdated ?? new Date()).toISOString())}
              </span>
            </div>
            <h1 className="mt-4 text-2xl font-semibold tracking-tight text-fg-primary sm:text-4xl">
              {payload.title}
            </h1>
            {payload.description && (
              <p className="mt-3 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-fg-secondary">
                {linkedText(payload.description)}
              </p>
            )}
          </div>

          <div className="w-full rounded-lg border border-border-subtle bg-bg-elevated p-4 lg:max-w-sm">
            <form onSubmit={subscribe} className="space-y-3">
              <div className="flex items-center gap-2 text-sm font-medium text-fg-primary">
                <Mail size={15} />
                Updates
              </div>
              <div className="flex gap-2">
                <Input
                  type="email"
                  value={email}
                  placeholder="email@example.com"
                  aria-label="Email address"
                  onChange={(event) => setEmail(event.target.value)}
                />
                <Button
                  type="submit"
                  size="md"
                  loading={submitting}
                  disabled={!email.trim()}
                >
                  Subscribe
                </Button>
              </div>
              <FormAlert message={subscribeError} />
              <FormAlert message={subscribeMessage} tone="success" />
            </form>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6">
        <section>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-muted">
              Components
            </h2>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              loading={refreshing}
              onClick={() => void load(true)}
            >
              <RefreshCw size={14} />
              Refresh
            </Button>
          </div>
          <div className="grid gap-3">
            {payload.components.length === 0 ? (
              <div className="rounded-lg border border-border-subtle bg-bg-panel p-4 text-sm text-fg-muted">
                No components published.
              </div>
            ) : (
              payload.components.map((component) => {
                const meta = STATUS_META[component.status];
                const Icon = meta.icon;
                return (
                  <article
                    key={component.service_id}
                    className="rounded-lg border border-border-subtle bg-bg-panel p-4 shadow-sm"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <h3 className="text-sm font-semibold text-fg-primary">
                        {component.display_name}
                      </h3>
                      <span className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium ${meta.className}`}>
                        <Icon size={13} />
                        {meta.label}
                      </span>
                    </div>
                    {component.uptime_90d && component.uptime_90d.length > 0 && (
                      <div className="mt-4">
                        <div className="mb-1 flex items-center justify-between text-xs text-fg-muted">
                          <span>90 day uptime</span>
                          <span>
                            {component.uptime_90d[
                              component.uptime_90d.length - 1
                            ]?.pct.toFixed(3)}
                            %
                          </span>
                        </div>
                        <div className="flex h-8 items-end gap-0.5 overflow-hidden rounded bg-bg-elevated p-1">
                          {component.uptime_90d.map((day) => (
                            <span
                              key={day.date}
                              title={`${day.date}: ${day.pct.toFixed(3)}%`}
                              className={`min-w-[3px] flex-1 rounded-sm ${uptimeTone(day.pct)}`}
                              style={{ height: `${Math.max(12, Math.min(100, day.pct))}%` }}
                              data-status={uptimeTone(day.pct)}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                  </article>
                );
              })
            )}
          </div>
        </section>

        <section className="grid gap-6 lg:grid-cols-2">
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-fg-muted">
              Active incidents
            </h2>
            <div className="space-y-3">
              {payload.active_incidents.length === 0 ? (
                <div className="rounded-lg border border-border-subtle bg-bg-panel p-4 text-sm text-fg-muted">
                  No active incidents.
                </div>
              ) : (
                payload.active_incidents.map((incident) => (
                  <IncidentBlock key={incident.id} incident={incident} />
                ))
              )}
            </div>
          </div>

          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-fg-muted">
              Recently resolved
            </h2>
            <div className="space-y-3">
              {payload.recently_resolved.length === 0 ? (
                <div className="rounded-lg border border-border-subtle bg-bg-panel p-4 text-sm text-fg-muted">
                  No recent resolutions.
                </div>
              ) : (
                payload.recently_resolved.map((incident) => (
                  <IncidentBlock key={incident.id} incident={incident} compact />
                ))
              )}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
