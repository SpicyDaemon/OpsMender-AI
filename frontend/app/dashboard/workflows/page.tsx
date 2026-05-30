"use client";

import { useCallback, useEffect, useState } from "react";
import { listWorkflowProfiles } from "@/lib/api";
import type { WorkflowProfileResponse } from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  ConfigPageSkeleton,
  WorkflowProfileSection,
} from "@/components/config/ConfigSections";

export default function WorkflowsPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const [profiles, setProfiles] = useState<WorkflowProfileResponse[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const r = await listWorkflowProfiles().catch(() => ({ items: [], total: 0 }));
    setProfiles(r.items);
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  if (loading) return <ConfigPageSkeleton />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">Workflows</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Customise the agent loop&apos;s node order for advanced setups. Defaults work for most operators.
        </p>
      </div>
      <WorkflowProfileSection
        profiles={profiles}
        onReload={reload}
        canEdit={canEdit}
      />
    </div>
  );
}
