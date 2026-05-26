"use client";

import { useCallback, useEffect, useState } from "react";
import { listBotConnectors } from "@/lib/api";
import type { BotConnectorResponse } from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  BotConnectorSection,
  ConfigPageSkeleton,
} from "@/components/config/ConfigSections";

export default function BotConnectorsPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const [connectors, setConnectors] = useState<BotConnectorResponse[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const r = await listBotConnectors().catch(() => ({ items: [], total: 0 }));
    setConnectors(r.items);
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  if (loading) return <ConfigPageSkeleton />;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">Bot connectors</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Slack, Teams, Discord, and friends — outbound channels OpsMender uses to reach humans.
        </p>
      </div>
      <BotConnectorSection
        connectors={connectors}
        onReload={reload}
        canEdit={canEdit}
      />
    </div>
  );
}
