"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BarChart3, Download, RefreshCw } from "lucide-react";

import {
  downloadAnalyticsCsv,
  getNoiseAnalytics,
  getResponseAnalytics,
  listServices,
} from "@/lib/api";
import type {
  AlertsByHourPoint,
  NoiseAnalyticsResponse,
  ResponseAnalyticsResponse,
  ResponsePrioritySummary,
  ServiceResponse,
  TopNoisyService,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { Label, Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { formatDate } from "@/lib/formatDate";

type Preset = "7d" | "30d" | "90d";
type Tab = "noise" | "response";

const PRESETS: Array<{ value: Preset; label: string; days: number }> = [
  { value: "7d", label: "7 days", days: 7 },
  { value: "30d", label: "30 days", days: 30 },
  { value: "90d", label: "90 days", days: 90 },
];

function rangeForPreset(preset: Preset) {
  const item = PRESETS.find((candidate) => candidate.value === preset) ?? PRESETS[1];
  const to = new Date();
  const from = new Date(to.getTime() - item.days * 24 * 60 * 60 * 1000);
  return { from: from.toISOString(), to: to.toISOString() };
}

function formatCount(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatSeconds(value: number | null): string {
  if (value == null) return "—";
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function SummaryStat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm">
      <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
        {label}
      </p>
      <p className="mt-1.5 text-2xl font-semibold tracking-tight text-fg-primary">
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-fg-muted">{hint}</p> : null}
    </div>
  );
}

const CHART_WIDTH = 1000;
const CHART_HEIGHT = 190;
const PLOT_HEIGHT = 160;

function HourBarChart({ points }: { points: AlertsByHourPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const total = points.reduce((sum, point) => sum + point.alerts, 0);
  if (!total) {
    return (
      <div className="flex h-40 items-center justify-center rounded-md border border-dashed border-border-subtle text-xs text-fg-muted">
        No alerts in this range
      </div>
    );
  }
  const max = Math.max(...points.map((point) => point.alerts), 1);
  const barWidth = CHART_WIDTH / points.length;
  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        className="h-[190px] w-full overflow-visible"
        role="img"
        aria-label="Alerts by UTC hour of day"
      >
        {[0.25, 0.5, 0.75, 1].map((ratio) => (
          <line
            key={ratio}
            x1="0"
            x2={CHART_WIDTH}
            y1={PLOT_HEIGHT * ratio}
            y2={PLOT_HEIGHT * ratio}
            className="stroke-border-subtle"
            strokeDasharray={ratio === 1 ? undefined : "4 6"}
          />
        ))}
        {points.map((point, index) => {
          const height = (point.alerts / max) * (PLOT_HEIGHT - 12);
          return (
            <g key={point.hour}>
              <rect
                x={index * barWidth + 5}
                y={PLOT_HEIGHT - height}
                width={Math.max(8, barWidth - 10)}
                height={height}
                rx="3"
                className="fill-accent"
              />
              <rect
                x={index * barWidth}
                y="0"
                width={barWidth}
                height={PLOT_HEIGHT}
                fill="transparent"
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover((value) => (value === index ? null : value))}
              />
            </g>
          );
        })}
        {[0, 6, 12, 18, 23].map((hour) => (
          <text
            key={hour}
            x={(hour / 23) * CHART_WIDTH}
            y={CHART_HEIGHT - 4}
            textAnchor={hour === 0 ? "start" : hour === 23 ? "end" : "middle"}
            className="fill-fg-muted text-[10px]"
          >
            {hour}:00
          </text>
        ))}
      </svg>
      {hover != null && points[hover] ? (
        <div
          className="pointer-events-none absolute top-1 z-10 -translate-x-1/2 rounded-md border border-border-strong bg-bg-elevated px-2.5 py-2 text-[11px] shadow-lg"
          style={{
            left: `${Math.min(92, Math.max(8, ((hover + 0.5) / points.length) * 100))}%`,
          }}
        >
          <div className="font-medium text-fg-primary">
            {points[hover].hour}:00 UTC
          </div>
          <div className="mt-1 text-fg-secondary">
            {formatCount(points[hover].alerts)} alerts
          </div>
        </div>
      ) : null}
    </div>
  );
}

function TrendChart({
  report,
}: {
  report: ResponseAnalyticsResponse;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const series = report.weekly_trend;
  const values = series.flatMap((point) =>
    [point.mtta_seconds, point.mttr_seconds].filter(
      (value): value is number => value != null,
    ),
  );
  if (!values.length) {
    return (
      <div className="flex h-40 items-center justify-center rounded-md border border-dashed border-border-subtle text-xs text-fg-muted">
        No acknowledged or resolved incidents in this range
      </div>
    );
  }
  const max = Math.max(...values, 1);
  const x = (index: number) =>
    series.length <= 1 ? CHART_WIDTH / 2 : (index / (series.length - 1)) * CHART_WIDTH;
  const y = (value: number) => PLOT_HEIGHT - (value / max) * (PLOT_HEIGHT - 12);
  const pathFor = (key: "mtta_seconds" | "mttr_seconds") =>
    series
      .map((point, index) =>
        point[key] == null ? null : `${x(index)},${y(point[key] ?? 0)}`,
      )
      .filter(Boolean)
      .join(" L ");
  const mttaPath = pathFor("mtta_seconds");
  const mttrPath = pathFor("mttr_seconds");

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        className="h-[190px] w-full overflow-visible"
        role="img"
        aria-label="MTTA and MTTR weekly trend"
      >
        {[0.25, 0.5, 0.75, 1].map((ratio) => (
          <line
            key={ratio}
            x1="0"
            x2={CHART_WIDTH}
            y1={PLOT_HEIGHT * ratio}
            y2={PLOT_HEIGHT * ratio}
            className="stroke-border-subtle"
            strokeDasharray={ratio === 1 ? undefined : "4 6"}
          />
        ))}
        {mttaPath ? (
          <path
            d={`M ${mttaPath}`}
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="3"
            className="text-status-info"
          />
        ) : null}
        {mttrPath ? (
          <path
            d={`M ${mttrPath}`}
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="3"
            className="text-status-high"
          />
        ) : null}
        {series.map((point, index) => (
          <rect
            key={point.week_start}
            x={(index / Math.max(1, series.length)) * CHART_WIDTH}
            y="0"
            width={CHART_WIDTH / Math.max(1, series.length)}
            height={PLOT_HEIGHT}
            fill="transparent"
            onMouseEnter={() => setHover(index)}
            onMouseLeave={() => setHover((value) => (value === index ? null : value))}
          />
        ))}
        {series.map((point, index) => (
          <text
            key={point.week_start}
            x={x(index)}
            y={CHART_HEIGHT - 4}
            textAnchor={index === 0 ? "start" : index === series.length - 1 ? "end" : "middle"}
            className="fill-fg-muted text-[10px]"
          >
            {formatDate(point.week_start)}
          </text>
        ))}
      </svg>
      <div className="mt-2 flex items-center gap-4 text-xs text-fg-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-4 rounded-sm bg-status-info" /> MTTA
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-4 rounded-sm bg-status-high" /> MTTR
        </span>
      </div>
      {hover != null && series[hover] ? (
        <div
          className="pointer-events-none absolute top-1 z-10 -translate-x-1/2 rounded-md border border-border-strong bg-bg-elevated px-2.5 py-2 text-[11px] shadow-lg"
          style={{
            left: `${Math.min(92, Math.max(8, ((hover + 0.5) / series.length) * 100))}%`,
          }}
        >
          <div className="font-medium text-fg-primary">
            Week of {formatDate(series[hover].week_start)}
          </div>
          <div className="mt-1 text-fg-secondary">
            MTTA {formatSeconds(series[hover].mtta_seconds)}
          </div>
          <div className="text-fg-secondary">
            MTTR {formatSeconds(series[hover].mttr_seconds)}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function AnalyticsPage() {
  const [tab, setTab] = useState<Tab>("noise");
  const [preset, setPreset] = useState<Preset>("30d");
  const [serviceId, setServiceId] = useState("");
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [noise, setNoise] = useState<NoiseAnalyticsResponse | null>(null);
  const [response, setResponse] = useState<ResponseAnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const toast = useToast();
  const range = useMemo(() => rangeForPreset(preset), [preset]);

  const params = useMemo(
    () => ({
      ...range,
      service_id: serviceId || undefined,
    }),
    [range, serviceId],
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [serviceData, noiseData, responseData] = await Promise.all([
        listServices().catch(() => ({ items: [], total: 0 })),
        getNoiseAnalytics(params),
        getResponseAnalytics(params),
      ]);
      setServices(serviceData.items);
      setNoise(noiseData);
      setResponse(responseData);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Analytics failed to load");
    } finally {
      setLoading(false);
    }
  }, [params, toast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const exportCsv = async (kind: "noise" | "response") => {
    try {
      const blob = await downloadAnalyticsCsv(kind, params);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `opsmender-${kind}-analytics.csv`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Export failed");
    }
  };

  const noisyColumns: DataTableColumn<TopNoisyService>[] = [
    {
      id: "service",
      label: "Service",
      accessor: (row) => row.service_name,
      sortable: true,
    },
    {
      id: "alerts",
      label: "Alerts",
      accessor: (row) => row.inbound_alerts,
      cell: (row) => formatCount(row.inbound_alerts),
      sortable: true,
    },
    {
      id: "created",
      label: "Created",
      accessor: (row) => row.incidents_created,
      cell: (row) => formatCount(row.incidents_created),
      sortable: true,
    },
    {
      id: "ratio",
      label: "Alerts / Created",
      accessor: (row) => row.alerts_per_created_incident,
      cell: (row) => row.alerts_per_created_incident.toFixed(2),
      sortable: true,
    },
  ];

  const priorityColumns: DataTableColumn<ResponsePrioritySummary>[] = [
    {
      id: "priority",
      label: "Priority",
      accessor: (row) => row.priority,
      cell: (row) => <Badge variant="default">{row.priority}</Badge>,
      sortable: true,
    },
    {
      id: "incidents",
      label: "Incidents",
      accessor: (row) => row.incident_count,
      sortable: true,
    },
    {
      id: "mtta",
      label: "MTTA",
      accessor: (row) => row.mtta_seconds ?? -1,
      cell: (row) => formatSeconds(row.mtta_seconds),
      sortable: true,
    },
    {
      id: "mttr",
      label: "MTTR",
      accessor: (row) => row.mttr_seconds ?? -1,
      cell: (row) => formatSeconds(row.mttr_seconds),
      sortable: true,
    },
  ];

  return (
    <div className="flex h-full max-h-screen flex-col overflow-y-auto bg-bg-base">
      <PageHeader
        title="Analytics"
        subtitle="Noise and response reports across recent incident operations."
        icon={<BarChart3 size={18} aria-hidden="true" />}
        actions={
          <Button variant="secondary" onClick={loadData} loading={loading}>
            <RefreshCw size={14} aria-hidden="true" /> Refresh
          </Button>
        }
      />

      <main className="space-y-5 p-4 sm:p-6">
        <div className="flex flex-col gap-3 rounded-lg border border-border-subtle bg-bg-panel px-4 py-4 shadow-sm lg:flex-row lg:items-end lg:justify-between">
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Analytics report">
            {(["noise", "response"] as const).map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={tab === item}
                onClick={() => setTab(item)}
                className={`rounded-md border px-3 py-2 text-sm font-medium transition ${
                  tab === item
                    ? "border-accent bg-accent-bg text-accent-text"
                    : "border-border-subtle bg-bg-elevated text-fg-secondary hover:text-fg-primary"
                }`}
              >
                {item === "noise" ? "Noise" : "Response"}
              </button>
            ))}
          </div>

          <div className="grid gap-3 sm:grid-cols-[auto_minmax(220px,280px)]">
            <div>
              <Label htmlFor="analytics-range">Range</Label>
              <Select
                id="analytics-range"
                value={preset}
                onChange={(event) => setPreset(event.target.value as Preset)}
              >
                {PRESETS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="analytics-service">Service</Label>
              <Select
                id="analytics-service"
                value={serviceId}
                onChange={(event) => setServiceId(event.target.value)}
              >
                <option value="">All services</option>
                {services.map((service) => (
                  <option key={service.id} value={service.id}>
                    {service.name}
                  </option>
                ))}
              </Select>
            </div>
          </div>
        </div>

        {tab === "noise" && noise ? (
          <section className="space-y-5">
            <div className="flex justify-end">
              <Button variant="secondary" onClick={() => exportCsv("noise")}>
                <Download size={14} aria-hidden="true" /> Export CSV
              </Button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <SummaryStat label="Inbound Alerts" value={formatCount(noise.inbound_alerts)} />
              <SummaryStat label="Incidents Created" value={formatCount(noise.incidents_created)} />
              <SummaryStat label="Noise Reduction" value={formatPercent(noise.noise_reduction_ratio)} />
              <SummaryStat label="Grouped Savings" value={formatCount(noise.grouped_alert_savings)} />
              <SummaryStat label="Flapping Incidents" value={formatCount(noise.flapping_incident_count)} />
            </div>
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
              <div className="rounded-lg border border-border-subtle bg-bg-panel p-4 shadow-sm">
                <div className="mb-3">
                  <h2 className="text-sm font-semibold text-fg-primary">
                    Alerts By Hour
                  </h2>
                  <p className="text-xs text-fg-muted">{noise.hour_display_caveat}</p>
                </div>
                <HourBarChart points={noise.alerts_by_hour_utc} />
              </div>
              <div className="rounded-lg border border-border-subtle bg-bg-panel p-4 shadow-sm">
                <h2 className="mb-3 text-sm font-semibold text-fg-primary">
                  Noisiest Services
                </h2>
                {noise.top_noisy_services.length ? (
                  <DataTable<TopNoisyService>
                    rows={noise.top_noisy_services}
                    columns={noisyColumns}
                    rowKey={(row: TopNoisyService) =>
                      row.service_id ?? row.service_name
                    }
                  />
                ) : (
                  <EmptyState
                    icon={BarChart3}
                    title="No noise data"
                    description="No inbound alerts match this range."
                  />
                )}
              </div>
            </div>
          </section>
        ) : null}

        {tab === "response" && response ? (
          <section className="space-y-5">
            <div className="flex justify-end">
              <Button variant="secondary" onClick={() => exportCsv("response")}>
                <Download size={14} aria-hidden="true" /> Export CSV
              </Button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <SummaryStat
                label="Incidents"
                value={formatCount(response.overall.incident_count)}
              />
              <SummaryStat
                label="Acknowledged"
                value={formatCount(response.overall.acknowledged_count)}
              />
              <SummaryStat
                label="MTTA"
                value={formatSeconds(response.overall.mtta_seconds)}
              />
              <SummaryStat
                label="MTTR"
                value={formatSeconds(response.overall.mttr_seconds)}
              />
            </div>
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
              <div className="rounded-lg border border-border-subtle bg-bg-panel p-4 shadow-sm">
                <h2 className="mb-3 text-sm font-semibold text-fg-primary">
                  Weekly Trend
                </h2>
                <TrendChart report={response} />
              </div>
              <div className="rounded-lg border border-border-subtle bg-bg-panel p-4 shadow-sm">
                <h2 className="mb-3 text-sm font-semibold text-fg-primary">
                  Priority Breakdown
                </h2>
                <DataTable<ResponsePrioritySummary>
                  rows={response.per_priority}
                  columns={priorityColumns}
                  rowKey={(row: ResponsePrioritySummary) => row.priority}
                />
              </div>
            </div>
          </section>
        ) : null}

        {!loading && !noise && !response ? (
          <EmptyState
            icon={BarChart3}
            title="Analytics unavailable"
            description="The reports could not be loaded."
          />
        ) : null}
      </main>
    </div>
  );
}
