"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getModelBootstrapStatus,
  listModelConfigs,
  listProviders,
} from "@/lib/api";
import type {
  ModelBootstrapStatusResponse,
  ModelConfigResponse,
  ProviderModelsResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  ConfigPageSkeleton,
  ModelSection,
} from "@/components/config/ConfigSections";

export default function ModelsPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const [providers, setProviders] = useState<ProviderModelsResponse[]>([]);
  const [configs, setConfigs] = useState<ModelConfigResponse[]>([]);
  const [bootstrap, setBootstrap] = useState<ModelBootstrapStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const [p, c, b] = await Promise.all([
      listProviders(),
      listModelConfigs(),
      getModelBootstrapStatus(),
    ]);
    setProviders(p.items);
    setConfigs(c.items);
    setBootstrap(b);
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  if (loading || !bootstrap) return <ConfigPageSkeleton />;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">Models</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          BYOM. Configure provider profiles for the agent loop.
        </p>
      </div>
      <ModelSection
        bootstrap={bootstrap}
        providers={providers}
        configs={configs}
        onReload={reload}
        canEdit={canEdit}
      />
    </div>
  );
}
