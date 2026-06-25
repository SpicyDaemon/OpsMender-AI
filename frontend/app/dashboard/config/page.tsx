"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ChevronRight, SlidersHorizontal } from "lucide-react";
import { getConfig } from "@/lib/api";
import type { ConfigResponse } from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  ConfigPageSkeleton,
  RetentionSection,
  TierSection,
} from "@/components/config/ConfigSections";
import { EmailSettingsSection } from "@/components/EmailSettingsSection";

export default function ConfigPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setConfig(await getConfig());
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  if (loading || !config) return <ConfigPageSkeleton />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">Settings</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Runtime defaults and storage retention. Models, MCP servers, and agent teams live under AI Agent; notification channels and outbound hooks live under Paging &amp; On-call.
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-primary">
          Runtime defaults
        </h2>
        <TierSection config={config} onSaved={reload} canEdit={canEdit} />
      </section>

      {user?.primary_org_id && <EmailSettingsSection orgId={user.primary_org_id} />}

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-primary">
          Storage &amp; retention
        </h2>
        <RetentionSection canEdit={canEdit} />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-primary">
          Advanced
        </h2>
        <Link
          href="/dashboard/workflows"
          className="flex items-center justify-between gap-4 rounded-xl border border-border-subtle bg-bg-panel px-4 py-4 shadow-sm transition-colors hover:bg-bg-hover sm:px-6"
        >
          <div className="flex items-start gap-3">
            <SlidersHorizontal size={18} className="mt-0.5 shrink-0 text-fg-muted" />
            <div>
              <p className="text-sm font-semibold text-fg-primary">Session Profiles</p>
              <p className="mt-0.5 text-sm text-fg-secondary">
                Control how an AI incident session runs — read-only investigation,
                standard assisted response, fast triage, and more. Most teams only
                need the default.
              </p>
            </div>
          </div>
          <ChevronRight size={18} className="shrink-0 text-fg-muted" />
        </Link>
      </section>
    </div>
  );
}
