/**
 * Session Orchestration overview — renders per-model occupancy, the queue,
 * and running sessions from the orchestration API.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { OrchestrationOverview } from "@/lib/types";

const { overview } = vi.hoisted(() => {
  const overview: OrchestrationOverview = {
  models: [
    {
      model_config_id: "m1",
      name: "Fast Haiku",
      provider: "anthropic",
      model_id: "claude-haiku-4-5",
      max_concurrent_sessions: 2,
      running: 2,
    },
    {
      model_config_id: "m2",
      name: "Strong Opus",
      provider: "anthropic",
      model_id: "claude-opus-4-8",
      max_concurrent_sessions: null,
      running: 1,
    },
  ],
  active_sessions: [
    {
      session_id: "s1",
      incident_id: "i1",
      incident_title: "AWS Critical outage",
      priority: "P0",
      status: "active",
      tier: 1,
      model_config_id: "m2",
      model_name: "Strong Opus",
      queued_at: null,
      queue_expires_at: null,
      queue_reason: null,
      force_started: false,
      started_at: new Date().toISOString(),
    },
  ],
  queued_sessions: [
    {
      session_id: "s2",
      incident_id: "i2",
      incident_title: "AWS Moderate latency",
      priority: "P1",
      status: "queued",
      tier: 2,
      model_config_id: null,
      model_name: null,
      queued_at: new Date().toISOString(),
      queue_expires_at: new Date(Date.now() + 600000).toISOString(),
      queue_reason: "All preferred models at capacity",
      force_started: false,
      started_at: new Date().toISOString(),
    },
  ],
  active_total: 1,
  queued_total: 1,
  };
  return { overview };
});

vi.mock("@/lib/api", () => ({
  getSessionOrchestration: vi.fn().mockResolvedValue(overview),
}));

import OrchestrationPage from "@/app/dashboard/orchestration/page";

describe("Session Orchestration page", () => {
  it("renders model occupancy, the queue, and running sessions", async () => {
    render(<OrchestrationPage />);

    await waitFor(() =>
      expect(screen.getAllByTestId("model-capacity-row").length).toBe(2),
    );

    // Occupancy text: capped model shows 2/2, unlimited shows 1/∞.
    expect(screen.getByText("2/2")).toBeTruthy();
    expect(screen.getByText("1/∞")).toBeTruthy();

    // Queue + running rows.
    expect(screen.getByTestId("queued-session-row")).toBeTruthy();
    expect(screen.getByText("All preferred models at capacity")).toBeTruthy();
    expect(screen.getByTestId("active-session-row")).toBeTruthy();
    expect(screen.getByText("AWS Critical outage")).toBeTruthy();
  });
});
