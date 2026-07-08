"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  ExternalLink,
  Globe2,
  LockKeyhole,
  Save,
  Trash2,
} from "lucide-react";
import {
  deleteStatusPageSubscriber,
  getStatusPageSettings,
  listServices,
  listStatusPageComponents,
  listStatusPageSubscribers,
  replaceStatusPageComponents,
  updateStatusPageSettings,
} from "@/lib/api";
import type {
  ServiceResponse,
  StatusPageComponentResponse,
  StatusPageSettingsResponse,
  StatusPageSubscriberResponse,
  StatusPageVisibility,
} from "@/lib/types";
import { ConfigCard } from "@/components/config/ConfigSections";
import { Button } from "@/components/ui/Button";
import { FormAlert, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { MultiSelect, type MultiSelectOption } from "@/components/ui/MultiSelect";
import { formatDateTime } from "@/lib/formatDate";

function defaultSettings(): StatusPageSettingsResponse {
  return {
    enabled: false,
    visibility: "private",
    title: "",
    description: "",
  };
}

export function StatusPageSettingsSection({ canEdit }: { canEdit: boolean }) {
  const [settings, setSettings] = useState<StatusPageSettingsResponse>(defaultSettings);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [components, setComponents] = useState<StatusPageComponentResponse[]>([]);
  const [selectedServices, setSelectedServices] = useState<string[]>([]);
  const [subscribers, setSubscribers] = useState<StatusPageSubscriberResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingSettings, setSavingSettings] = useState(false);
  const [savingComponents, setSavingComponents] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setError("");
    const [settingsRes, componentsRes, servicesRes, subscribersRes] =
      await Promise.all([
        getStatusPageSettings(),
        listStatusPageComponents(),
        listServices(),
        listStatusPageSubscribers().catch(() => ({ items: [], total: 0 })),
      ]);
    setSettings(settingsRes);
    setComponents(componentsRes.items);
    setSelectedServices(componentsRes.items.map((component) => component.service_id));
    setServices(servicesRes.items);
    setSubscribers(subscribersRes.items);
  }, []);

  useEffect(() => {
    reload()
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Status page settings failed to load.");
      })
      .finally(() => setLoading(false));
  }, [reload]);

  const serviceOptions = useMemo<MultiSelectOption[]>(
    () =>
      services.map((service) => ({
        value: service.id,
        label: service.name,
        sublabel: service.is_active ? service.priority : `${service.priority} · inactive`,
        disabled: !service.is_active,
      })),
    [services],
  );

  async function saveSettings() {
    setSavingSettings(true);
    setError("");
    setMessage("");
    try {
      const saved = await updateStatusPageSettings({
        enabled: settings.enabled,
        visibility: settings.visibility,
        title: settings.title?.trim() || null,
        description: settings.description?.trim() || null,
      });
      setSettings(saved);
      setMessage("Status page settings saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Status page settings were not saved.");
    } finally {
      setSavingSettings(false);
    }
  }

  async function saveComponents() {
    setSavingComponents(true);
    setError("");
    setMessage("");
    try {
      const saved = await replaceStatusPageComponents(selectedServices);
      setComponents(saved.items);
      setSelectedServices(saved.items.map((component) => component.service_id));
      setMessage("Status page components saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Status page components were not saved.");
    } finally {
      setSavingComponents(false);
    }
  }

  async function removeSubscriber(subscriber: StatusPageSubscriberResponse) {
    if (!window.confirm(`Remove ${subscriber.email} from status updates?`)) return;
    setError("");
    setMessage("");
    try {
      await deleteStatusPageSubscriber(subscriber.id);
      setSubscribers((current) =>
        current.filter((candidate) => candidate.id !== subscriber.id),
      );
      setMessage("Subscriber removed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Subscriber was not removed.");
    }
  }

  const componentCount = components.length;
  const confirmedCount = subscribers.filter((subscriber) => subscriber.confirmed_at).length;

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-primary">
          Status Page
        </h2>
        <Link
          href="/status"
          target="_blank"
          className="inline-flex items-center gap-1.5 rounded-md border border-border-strong bg-bg-panel px-3 py-1.5 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
        >
          <ExternalLink size={14} />
          View
        </Link>
      </div>

      <ConfigCard
        title="Public incident communications"
        description={`${componentCount} component${componentCount === 1 ? "" : "s"} · ${confirmedCount} confirmed subscriber${confirmedCount === 1 ? "" : "s"}`}
      >
        {loading ? (
          <div className="space-y-3">
            <div className="h-9 animate-pulse rounded-md bg-bg-hover" />
            <div className="h-24 animate-pulse rounded-md bg-bg-hover" />
          </div>
        ) : (
          <div className="space-y-5">
            <FormAlert message={error} />
            <FormAlert message={message} tone="success" />

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,0.45fr)]">
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                  <label className="inline-flex items-center gap-2 text-sm text-fg-primary">
                    <input
                      type="checkbox"
                      checked={settings.enabled}
                      disabled={!canEdit || savingSettings}
                      onChange={(event) =>
                        setSettings((current) => ({
                          ...current,
                          enabled: event.target.checked,
                        }))
                      }
                    />
                    Enabled
                  </label>
                  <span className="inline-flex items-center gap-1.5 rounded-md border border-border-subtle bg-bg-elevated px-2 py-1 text-xs text-fg-secondary">
                    {settings.visibility === "public" ? (
                      <Globe2 size={13} />
                    ) : (
                      <LockKeyhole size={13} />
                    )}
                    {settings.visibility}
                  </span>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <Label htmlFor="status-page-title">Title</Label>
                    <Input
                      id="status-page-title"
                      value={settings.title ?? ""}
                      disabled={!canEdit || savingSettings}
                      onChange={(event) =>
                        setSettings((current) => ({
                          ...current,
                          title: event.target.value,
                        }))
                      }
                    />
                  </div>
                  <div>
                    <Label htmlFor="status-page-visibility">Visibility</Label>
                    <Select
                      id="status-page-visibility"
                      value={settings.visibility}
                      disabled={!canEdit || savingSettings}
                      onChange={(event) =>
                        setSettings((current) => ({
                          ...current,
                          visibility: event.target.value as StatusPageVisibility,
                        }))
                      }
                    >
                      <option value="private">Private</option>
                      <option value="public">Public</option>
                    </Select>
                  </div>
                </div>

                <div>
                  <Label htmlFor="status-page-description">Description</Label>
                  <Textarea
                    id="status-page-description"
                    value={settings.description ?? ""}
                    disabled={!canEdit || savingSettings}
                    rows={3}
                    onChange={(event) =>
                      setSettings((current) => ({
                        ...current,
                        description: event.target.value,
                      }))
                    }
                  />
                </div>

                <div className="flex justify-end">
                  <Button
                    type="button"
                    onClick={saveSettings}
                    loading={savingSettings}
                    disabled={!canEdit}
                  >
                    <Save size={14} />
                    Save settings
                  </Button>
                </div>
              </div>

              <div className="space-y-4 rounded-lg border border-border-subtle bg-bg-elevated p-4">
                <div>
                  <Label>Components</Label>
                  <MultiSelect
                    options={serviceOptions}
                    selected={selectedServices}
                    ordered
                    ariaLabel="Status page services"
                    emptyLabel="No services available."
                    onChange={setSelectedServices}
                  />
                </div>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={saveComponents}
                  loading={savingComponents}
                  disabled={!canEdit}
                  className="w-full"
                >
                  <Save size={14} />
                  Save components
                </Button>
              </div>
            </div>

            <div className="rounded-lg border border-border-subtle">
              <div className="flex items-center justify-between gap-3 border-b border-border-subtle px-3 py-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
                  Subscribers
                </p>
                <span className="text-xs text-fg-muted">{subscribers.length}</span>
              </div>
              {subscribers.length === 0 ? (
                <p className="px-3 py-4 text-sm text-fg-muted">
                  No subscribers.
                </p>
              ) : (
                <ul className="divide-y divide-border-subtle">
                  {subscribers.map((subscriber) => (
                    <li
                      key={subscriber.id}
                      className="flex flex-wrap items-center justify-between gap-3 px-3 py-2"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-fg-primary">
                          {subscriber.email}
                        </p>
                        <p className="mt-0.5 flex items-center gap-1.5 text-xs text-fg-muted">
                          {subscriber.confirmed_at ? (
                            <>
                              <CheckCircle2 size={12} />
                              Confirmed {formatDateTime(subscriber.confirmed_at)}
                            </>
                          ) : (
                            "Pending confirmation"
                          )}
                        </p>
                      </div>
                      {canEdit && (
                        <button
                          type="button"
                          title={`Remove ${subscriber.email}`}
                          aria-label={`Remove ${subscriber.email}`}
                          onClick={() => void removeSubscriber(subscriber)}
                          className="rounded-md p-1.5 text-fg-muted hover:bg-status-critical-bg hover:text-status-critical"
                        >
                          <Trash2 size={15} />
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </ConfigCard>
    </section>
  );
}
