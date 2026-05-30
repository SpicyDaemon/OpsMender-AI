"use client";

import { useCallback, useEffect, useState } from "react";
import { listAgentTeamProfiles } from "@/lib/api";
import type { AgentTeamProfileResponse } from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  AgentTeamProfileSection,
  ConfigPageSkeleton,
} from "@/components/config/ConfigSections";

export default function AgentTeamsPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const [profiles, setProfiles] = useState<AgentTeamProfileResponse[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const r = await listAgentTeamProfiles().catch(() => ({ items: [], total: 0 }));
    setProfiles(r.items);
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  if (loading) return <ConfigPageSkeleton />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">Agent teams</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Saved OpsMender role profiles for multi-agent workflows.
        </p>
      </div>
      <AgentTeamProfileSection
        profiles={profiles}
        onReload={reload}
        canEdit={canEdit}
      />
    </div>
  );
}
