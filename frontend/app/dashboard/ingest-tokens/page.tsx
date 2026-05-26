"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getConfig,
  listIngestProviders,
  listIngestTokens,
} from "@/lib/api";
import type {
  ConfigResponse,
  IngestProviderItem,
  IngestTokenResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  ConfigPageSkeleton,
  IngestAutoStartSection,
  IngestTokenSection,
} from "@/components/config/ConfigSections";

export default function IngestTokensPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [tokens, setTokens] = useState<IngestTokenResponse[]>([]);
  const [providers, setProviders] = useState<IngestProviderItem[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const [c, t, p] = await Promise.all([
      getConfig(),
      listIngestTokens().catch(() => ({ items: [], total: 0 })),
      listIngestProviders().catch(() => ({ items: [] })),
    ]);
    setConfig(c);
    setTokens(t.items);
    setProviders(p.items);
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  if (loading || !config) return <ConfigPageSkeleton />;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">Ingest tokens</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Inbound credentials for external monitors (CloudWatch, LegacyAlertVendor, Alertmanager, custom scripts) to POST incidents in.
        </p>
      </div>
      <IngestAutoStartSection
        config={config}
        onSaved={reload}
        canEdit={canEdit}
      />
      <IngestTokenSection
        tokens={tokens}
        ingestProviders={providers}
        onReload={reload}
        canEdit={canEdit}
      />
    </div>
  );
}
