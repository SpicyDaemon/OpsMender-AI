/**
 * F2: a live approval event names its request by the id the server sends
 * (`id`), not an `approval_id` field that never arrives, so the sidecar no
 * longer prints "ID: undefined".
 */
import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: "a", username: "admin", role: "admin" } }),
}));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

type Handler = (msg: { type: string; data: Record<string, unknown> }) => void;
const stream = vi.hoisted(() => ({ onMessage: null as Handler | null }));

const apiMocks = vi.hoisted(() => ({
  approveRequest: vi.fn(),
  rejectRequest: vi.fn(),
  sendSessionMessage: vi.fn(),
  getSession: vi.fn().mockResolvedValue({
    id: "sess-1",
    status: "active",
    tier: 1,
    incident_id: "inc-1",
    created_at: "2026-10-29T00:00:00Z",
    updated_at: "2026-10-29T00:00:00Z",
  }),
  listSessionMessages: vi.fn().mockResolvedValue({ items: [] }),
  listApprovals: vi.fn().mockResolvedValue({ items: [] }),
  connectSessionStream: vi.fn((_id: string, onMessage: Handler) => {
    stream.onMessage = onMessage;
    return { close: vi.fn() };
  }),
}));
vi.mock("@/lib/api", () => apiMocks);

import { IncidentSessionSidecar } from "./IncidentSessionSidecar";

// jsdom has no layout; the sidecar scrolls its logs into view on each event.
Element.prototype.scrollIntoView = vi.fn();

async function open() {
  render(
    <IncidentSessionSidecar sessionId="sess-1" onClose={() => {}} canControlSession canApproveActions />,
  );
  await waitFor(() => expect(stream.onMessage).not.toBeNull());
}

afterEach(() => {
  stream.onMessage = null;
});

describe("sidecar approval events", () => {
  it("shows the id the server sends for a requested and a resolved approval", async () => {
    await open();

    act(() => stream.onMessage!({ type: "approval_requested", data: { id: "req-77", status: "pending" } }));
    expect(await screen.findByText("ID: req-77")).toBeTruthy();

    act(() => stream.onMessage!({ type: "approval_resolved", data: { id: "req-77", status: "approved" } }));
    expect(await screen.findByText("Approval approved")).toBeTruthy();
    expect(screen.getByText("req-77")).toBeTruthy();
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it("shows where a later round's request came from beside its Approve button (M1-166)", async () => {
    apiMocks.listApprovals.mockResolvedValueOnce({
      items: [
        {
          id: "req-80",
          session_id: "sess-1",
          action: { tool_name: "restart_deployment" },
          justification: "The log shows the pod is out of memory.",
          status: "pending",
          evidence: {
            source: "tool_results",
            round: 2,
            calls: [{ tool_name: "get_pod_logs", outcome: "ok", round: 1 }],
            calls_not_shown: 0,
            addressed: [],
          },
        },
      ],
    });
    await open();

    const note = await screen.findByRole("note", { name: "Where this request came from" });
    expect(note.textContent).toContain("Written after reading tool results");
    expect(note.textContent).toContain("get_pod_logs: returned a result");
  });

  it("still accepts a producer that names the field approval_id", async () => {
    await open();

    act(() => stream.onMessage!({ type: "approval_requested", data: { approval_id: "req-78" } }));
    expect(await screen.findByText("ID: req-78")).toBeTruthy();
  });
});
