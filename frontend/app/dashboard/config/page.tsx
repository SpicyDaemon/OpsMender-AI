"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Bell, ChevronRight, Cpu, Network } from "lucide-react";
import { getConfig } from "@/lib/api";
import type { ConfigResponse } from "@/lib/types";
import { useAuth } from "@/context/auth";
import {
  ConfigPageSkeleton,
  RetentionSection,
  TierSection,
  WorkflowSettingsSection,
} from "@/components/config/ConfigSections";
import { ApiTokensSection } from "@/components/config/ApiTokensSection";
import { EmailSettingsSection } from "@/components/EmailSettingsSection";
import { VoiceSettingsSection } from "@/components/VoiceSettingsSection";
import { OrganizationSettingsSection } from "@/components/OrganizationSettingsSection";

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
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-fg-primary sm:text-2xl">Settings</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Runtime defaults, workspace identity, authentication, SMTP, calling, and retention for this OpsMender instance.
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {[
          {
            href: "/dashboard/models",
            icon: Cpu,
            title: "Models",
            description: "Provider profiles and default model selection.",
          },
          {
            href: "/dashboard/mcp-servers",
            icon: Network,
            title: "MCP servers",
            description: "Tool server connections and runtime health.",
          },
          {
            href: "/dashboard/paging/notification-channels",
            icon: Bell,
            title: "Notification Channels",
            description: "Delivery channels and channel capabilities.",
          },
        ].map(({ href, icon: Icon, title, description }) => (
          <Link
            key={href}
            href={href}
            className="flex items-start justify-between gap-3 rounded-lg border border-border-subtle bg-bg-panel px-4 py-3 transition-colors hover:bg-bg-hover"
          >
            <span className="flex min-w-0 items-start gap-3">
              <Icon size={16} className="mt-0.5 shrink-0 text-fg-muted" />
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-fg-primary">{title}</span>
                <span className="mt-0.5 block text-xs text-fg-secondary">{description}</span>
              </span>
            </span>
            <ChevronRight size={16} className="mt-0.5 shrink-0 text-fg-muted" />
          </Link>
        ))}
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-primary">
          Runtime defaults
        </h2>
        <TierSection config={config} onSaved={reload} canEdit={canEdit} />
      </section>

      {canEdit && <WorkflowSettingsSection canEdit={canEdit} />}

      {user?.primary_org_id && (
        <OrganizationSettingsSection orgId={user.primary_org_id} />
      )}

      {user?.primary_org_id && <EmailSettingsSection orgId={user.primary_org_id} />}

      {canEdit && <VoiceSettingsSection />}

      {canEdit && <ApiTokensSection />}

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-primary">
          Storage &amp; retention
        </h2>
        <RetentionSection canEdit={canEdit} />
      </section>
    </div>
  );
}
