"use client";

import { useCallback, useEffect, useState } from "react";
import { listWebhookTriggers } from "@/lib/api";
import type { WebhookTriggerResponse } from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  ConfigPageSkeleton,
  WebhookTriggerSection,
} from "@/components/config/ConfigSections";

export function OutboundHooksPage() {
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
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">
          Outbound Hooks
        </h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Send incident and session lifecycle updates to external systems or
          stakeholder workflows when state changes. Use My Notifications for
          individual on-call delivery preferences.
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
