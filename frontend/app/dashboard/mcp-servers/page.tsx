"use client";

import { useCallback, useEffect, useState } from "react";
import { listMCPServers, listMCPServerStatuses } from "@/lib/api";
import type {
  MCPServerResponse,
  MCPServerStatusResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  ConfigPageSkeleton,
  MCPSection,
} from "@/components/config/ConfigSections";

export default function MCPServersPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const [servers, setServers] = useState<MCPServerResponse[]>([]);
  const [statuses, setStatuses] = useState<MCPServerStatusResponse[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const [s, st] = await Promise.all([
      listMCPServers(),
      listMCPServerStatuses().catch(() => ({ items: [], total: 0 })),
    ]);
    setServers(s.items);
    setStatuses(st.items);
  }, []);

  const reloadStatuses = useCallback(async () => {
    const st = await listMCPServerStatuses().catch(() => ({ items: [], total: 0 }));
    setStatuses(st.items);
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void reloadStatuses();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [reloadStatuses]);

  if (loading) return <ConfigPageSkeleton />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">MCP servers</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Tool surface for the agent. Stdio, SSE, or HTTP — with optional OAuth.
        </p>
      </div>
      <MCPSection
        servers={servers}
        statuses={statuses}
        onReload={reload}
        onStatusReload={reloadStatuses}
        canEdit={canEdit}
      />
    </div>
  );
}
