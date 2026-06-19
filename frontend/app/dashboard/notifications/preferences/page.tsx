"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, BellRing, Save } from "lucide-react";
import {
  getNotificationPreferences,
  updateNotificationPreferences,
  type NotificationPreferences,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { FormError, Input, Label } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { Toggle } from "@/components/ui/Toggle";
import { useToast } from "@/components/ui/Toast";

const CATEGORY_LABELS: Record<string, string> = {
  incident: "Incidents",
  approval: "Approvals",
  session: "AI sessions",
  mention: "Mentions",
  reliability: "Reliability / SLO",
  account: "Account",
};

function categoryLabel(category: string) {
  return CATEGORY_LABELS[category] ?? category;
}

function browserTz() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export default function NotificationPreferencesPage() {
  const toast = useToast();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const [categories, setCategories] = useState<string[]>([]);
  // muted set drives the toggles: a category present here is OFF (muted).
  const [muted, setMuted] = useState<Set<string>>(new Set());

  const [quietEnabled, setQuietEnabled] = useState(false);
  const [quietStart, setQuietStart] = useState("22:00");
  const [quietEnd, setQuietEnd] = useState("07:00");
  const [quietTz, setQuietTz] = useState(browserTz());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError("");
      try {
        const prefs = await getNotificationPreferences();
        if (cancelled) return;
        applyPrefs(prefs);
      } catch (err) {
        if (cancelled) return;
        setLoadError(
          err instanceof Error ? err.message : "Failed to load preferences",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyPrefs(prefs: NotificationPreferences) {
    setCategories(prefs.categories);
    setMuted(new Set(prefs.muted_categories));
    const qh = prefs.quiet_hours;
    if (qh) {
      setQuietEnabled(qh.enabled);
      if (qh.start) setQuietStart(qh.start);
      if (qh.end) setQuietEnd(qh.end);
      setQuietTz(qh.tz || browserTz());
    }
  }

  function toggleCategory(category: string, receive: boolean) {
    setMuted((prev) => {
      const next = new Set(prev);
      // receive ON => not muted; receive OFF => muted
      if (receive) next.delete(category);
      else next.add(category);
      return next;
    });
  }

  async function handleSave() {
    setSaving(true);
    setSaveError("");
    try {
      const prefs = await updateNotificationPreferences({
        muted_categories: categories.filter((c) => muted.has(c)),
        quiet_hours: {
          enabled: quietEnabled,
          start: quietStart,
          end: quietEnd,
          tz: quietTz,
        },
      });
      applyPrefs(prefs);
      toast.success("Notification preferences saved");
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader
        title="Notification preferences"
        subtitle="Choose which in-app notifications you receive and set quiet hours."
        icon={<BellRing size={18} />}
        actions={
          <Link
            href="/dashboard/notifications"
            className="inline-flex items-center gap-1.5 rounded-md border border-border-strong bg-bg-surface px-3 py-2 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
          >
            <ArrowLeft size={14} /> Back to inbox
          </Link>
        }
      />

      {loading ? (
        <TableSkeleton rows={6} columns={2} />
      ) : loadError ? (
        <div className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
          <FormError message={loadError} />
        </div>
      ) : (
        <>
          {/* Categories */}
          <section className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm sm:p-6">
            <h2 className="text-sm font-semibold text-fg-primary">Categories</h2>
            <p className="mt-0.5 text-sm text-fg-secondary">
              Turn a category off to mute its notifications. Muted categories no
              longer appear in your notification center or pop live.
            </p>
            <ul className="mt-4 divide-y divide-border-subtle">
              {categories.map((category) => {
                const receive = !muted.has(category);
                const id = `cat-${category}`;
                return (
                  <li
                    key={category}
                    className="flex items-center justify-between gap-4 py-3"
                  >
                    <Label htmlFor={id} className="mb-0">
                      {categoryLabel(category)}
                    </Label>
                    <Toggle
                      id={id}
                      checked={receive}
                      onChange={(checked) => toggleCategory(category, checked)}
                    />
                  </li>
                );
              })}
            </ul>
          </section>

          {/* Quiet hours */}
          <section className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm sm:p-6">
            <div className="flex items-center justify-between gap-4">
              <div>
                <h2 className="text-sm font-semibold text-fg-primary">
                  Quiet hours
                </h2>
                <p className="mt-0.5 text-sm text-fg-secondary">
                  During quiet hours we suppress the live pop and badge bump.
                  Notifications still appear in your notification center.
                </p>
              </div>
              <Toggle
                id="quiet-enabled"
                checked={quietEnabled}
                onChange={setQuietEnabled}
              />
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <Label htmlFor="quiet-start">Start</Label>
                <Input
                  id="quiet-start"
                  type="time"
                  value={quietStart}
                  onChange={(e) => setQuietStart(e.target.value)}
                  disabled={!quietEnabled}
                />
              </div>
              <div>
                <Label htmlFor="quiet-end">End</Label>
                <Input
                  id="quiet-end"
                  type="time"
                  value={quietEnd}
                  onChange={(e) => setQuietEnd(e.target.value)}
                  disabled={!quietEnabled}
                />
              </div>
              <div>
                <Label htmlFor="quiet-tz">Timezone</Label>
                <Input
                  id="quiet-tz"
                  value={quietTz}
                  onChange={(e) => setQuietTz(e.target.value)}
                  disabled={!quietEnabled}
                />
              </div>
            </div>
          </section>

          {saveError && <FormError message={saveError} />}

          <div className="flex justify-end">
            <Button onClick={handleSave} loading={saving}>
              <Save size={14} /> Save preferences
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
