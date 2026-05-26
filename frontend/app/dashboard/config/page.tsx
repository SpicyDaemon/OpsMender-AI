"use client";

import { useCallback, useEffect, useState } from "react";
import { getConfig } from "@/lib/api";
import type { ConfigResponse } from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  ConfigPageSkeleton,
  RetentionSection,
  TierSection,
} from "@/components/config/ConfigSections";

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
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">Config</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Runtime defaults and storage retention. Models, MCP servers, workflows, agent teams, bot connectors, webhook triggers, and ingest tokens each have their own page in the sidebar.
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-primary">
          Runtime defaults
        </h2>
        <TierSection config={config} onSaved={reload} canEdit={canEdit} />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-primary">
          Storage &amp; retention
        </h2>
        <RetentionSection canEdit={canEdit} />
      </section>
    </div>
  );
}
