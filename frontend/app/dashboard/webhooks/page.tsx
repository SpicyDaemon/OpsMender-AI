"use client";

import { useCallback, useEffect, useState } from "react";
import { listWebhookTriggers } from "@/lib/api";
import type { WebhookTriggerResponse } from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  ConfigPageSkeleton,
  WebhookTriggerSection,
} from "@/components/config/ConfigSections";

export default function WebhooksPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const [triggers, setTriggers] = useState<WebhookTriggerResponse[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const r = await listWebhookTriggers().catch(() => ({ items: [], total: 0 }));
    setTriggers(r.items);
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  if (loading) return <ConfigPageSkeleton />;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold text-fg-primary">Webhook triggers</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Outbound notifications fired on incident/session lifecycle events.
        </p>
      </div>
      <WebhookTriggerSection
        triggers={triggers}
        onReload={reload}
        canEdit={canEdit}
      />
    </div>
  );
}
