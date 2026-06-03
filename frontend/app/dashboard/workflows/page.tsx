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
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">
            Session Profiles
          </h1>
          <span className="rounded-full border border-border-subtle bg-bg-elevated px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
            Advanced
          </span>
        </div>
        <p className="mt-1 max-w-3xl text-sm text-fg-secondary">
          Session Profiles define how an AI incident session runs — for example a
          read-only investigation, a standard assisted response, or a fast triage.
          Most teams only need the built-in default. Creating a custom profile is
          an advanced option; safety rules (the tier gate that governs what the AI
          may execute) are always preserved.
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
