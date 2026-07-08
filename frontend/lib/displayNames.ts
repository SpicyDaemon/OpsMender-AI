type SessionLike = {
  id: string;
  incident_id?: string | null;
  summary?: string | null;
};

type IncidentLike = {
  id: string;
  title?: string | null;
};

const AUDIT_ENTRY_LABELS: Record<string, string> = {
  session_start: "Session started",
  session_end: "Session ended",
  pre: "Tool call",
  post: "Tool result",
};

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  azure_openai: "Azure OpenAI",
  openai_compatible: "OpenAI-compatible",
  vertex_ai: "Vertex AI",
  gcp_vertex: "Vertex AI",
  bedrock: "AWS Bedrock",
  ollama: "Ollama",
  anthropic: "Anthropic",
};

const WORKFLOW_NODE_LABELS: Record<string, string> = {
  observe: "Observe",
  diagnose: "Diagnose",
  plan: "Plan",
  tier_gate: "Tier gate",
  execute: "Execute",
  verify: "Verify",
  summarize: "Summarize",
};

const TIER_DISPLAY: Record<number, { label: string; className: string }> = {
  0: {
    label: "Autonomous",
    className: "bg-status-critical-bg text-status-critical border-status-critical-border",
  },
  1: {
    label: "Approval",
    className: "bg-status-high-bg text-status-high border-status-high-border",
  },
  2: {
    label: "Advisory",
    className: "bg-status-low-bg text-status-low border-status-low-border",
  },
  3: {
    label: "Advisory",
    className: "bg-status-low-bg text-status-low border-status-low-border",
  },
};

const ALERT_GROUPING_LABELS: Record<string, string> = {
  inherit: "Inherit (workspace default)",
  on: "On",
  off: "Off",
};

const ROLE_LABELS: Record<string, string> = {
  admin: "Admin",
  operator: "Operator",
  viewer: "Viewer",
};

export function titleCaseIdentifier(value: string | null | undefined): string {
  if (!value) return "Unknown";
  return value
    .replace(/[_-]/g, " ")
    .trim()
    .replace(/\s+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function auditEntryTypeLabel(value: string): string {
  return AUDIT_ENTRY_LABELS[value] ?? titleCaseIdentifier(value);
}

export function providerName(value: string): string {
  return PROVIDER_LABELS[value] ?? titleCaseIdentifier(value);
}

export function workflowNodeLabel(value: string): string {
  return WORKFLOW_NODE_LABELS[value] ?? titleCaseIdentifier(value);
}

export function autonomyTierDisplay(tier: number): { label: string; className: string } {
  return (
    TIER_DISPLAY[tier] ?? {
      label: `Tier ${tier}`,
      className: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
    }
  );
}

export function alertGroupingLabel(value: string | null | undefined): string {
  return ALERT_GROUPING_LABELS[value ?? "inherit"] ?? titleCaseIdentifier(value);
}

export function roleLabel(value: string | null | undefined): string {
  return ROLE_LABELS[value ?? ""] ?? titleCaseIdentifier(value);
}

export function groupedAlertBadgeLabel(count: number): string {
  return `×${count} grouped`;
}

export function flappingIncidentBadgeLabel(): string {
  return "Flapping";
}

export function sessionPrimaryLabel(
  session: SessionLike,
  incidentById: Map<string, IncidentLike>,
): string {
  if (session.incident_id) {
    const incident = incidentById.get(session.incident_id);
    if (incident?.title) return incident.title;
  }
  if (session.summary?.trim()) return session.summary.trim();
  return "Untitled session";
}
