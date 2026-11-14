import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const apiMocks = vi.hoisted(() => ({
  approveRequest: vi.fn(),
  extendApprovalRequest: vi.fn(),
  listApprovals: vi.fn(),
  rejectRequest: vi.fn(),
  redirectRequest: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import ApprovalsPage from "@/app/dashboard/approvals/page";

beforeEach(() => {
  apiMocks.approveRequest.mockResolvedValue({});
  apiMocks.extendApprovalRequest.mockResolvedValue({});
  apiMocks.listApprovals.mockResolvedValue({ items: [], total: 0 });
  apiMocks.rejectRequest.mockResolvedValue({});
  apiMocks.redirectRequest.mockResolvedValue({});
});

const LATER_ROUND_APPROVAL = {
  id: "approval-2",
  session_id: "session-12345678",
  session_tier: 1,
  action: { tool_name: "restart_deployment", tool_parameters: { deployment: "ratings-v1" } },
  justification: "The ratings log shows the pod is out of memory.",
  evidence: {
    source: "tool_results",
    round: 2,
    calls: [
      {
        tool_name: "get_pod_logs",
        tool_parameters: { service: "ratings" },
        outcome: "ok",
        round: 1,
      },
    ],
    calls_not_shown: 0,
    addressed: [],
    rejected: [],
  },
  status: "pending",
  resolution_note: null,
  requested_at: "2026-11-11T12:00:00Z",
  resolved_at: null,
  resolved_by: null,
  expires_at: "2026-11-11T12:10:00Z",
  extension_count: 0,
  extension_notified_at: null,
};

describe("Approvals page", () => {
  it("marks a request written after reading tool results wherever it can be approved (M1-166)", async () => {
    apiMocks.listApprovals.mockResolvedValue({ items: [LATER_ROUND_APPROVAL], total: 1 });

    render(<ApprovalsPage />);

    // The mobile card holds the note with the calls; the desktop row a hint.
    const note = await screen.findByRole("note", { name: "Where this request came from" });
    expect(note.textContent).toContain("get_pod_logs (service=ratings): returned a result");
    const marks = screen.getAllByText("Written after reading tool results");
    expect(marks).toHaveLength(2);
    const inTable = marks.filter((mark) => mark.closest("table"));
    expect(inTable).toHaveLength(1);

    // Opening the request from the desktop row (the action's name, not the
    // row's Approve button) shows the note in the dialog.
    const row = inTable[0].closest("tr") as HTMLElement;
    fireEvent.click(row.querySelector("button.text-left") as HTMLButtonElement);
    expect(apiMocks.approveRequest).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(
        screen.getAllByRole("note", { name: "Where this request came from" }),
      ).toHaveLength(2),
    );
  });

  it("uses a pending-specific empty state before the onboarding all-status branch", async () => {
    render(<ApprovalsPage />);

    expect(await screen.findByText("No approvals waiting")).toBeTruthy();
    expect(screen.getByText(/Tier 1 actions that need human sign-off will appear here/i)).toBeTruthy();
    expect(screen.queryByText(/Try a different status filter/i)).toBeNull();
    expect(screen.queryByRole("link", { name: /fire test incident/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /all statuses/i }));

    await waitFor(() => expect(apiMocks.listApprovals).toHaveBeenLastCalledWith({
      status: undefined,
      limit: 100,
    }));
    expect(await screen.findByText("No approvals yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: /fire test incident/i })).toBeTruthy();
  });

  it("renders mobile approval cards with collapsed action details and large actions", async () => {
    const approval = {
      id: "approval-1",
      session_id: "session-12345678",
      session_tier: 1,
      action: { tool: "restart_pod", namespace: "prod" },
      justification: "Pod is wedged after rollout.",
      status: "pending",
      resolution_note: null,
      requested_at: "2026-07-06T12:00:00Z",
      resolved_at: null,
      resolved_by: null,
      expires_at: "2026-07-06T12:10:00Z",
      extension_count: 0,
      extension_notified_at: null,
    };
    apiMocks.listApprovals.mockResolvedValue({ items: [approval], total: 1 });

    render(<ApprovalsPage />);

    expect(await screen.findAllByText("T1")).toHaveLength(2);
    expect(screen.getAllByText("Approval").length).toBeGreaterThan(0);
    // A first plan's request carries no mark.
    expect(screen.queryByText("Written after reading tool results")).toBeNull();

    const disclosure = await screen.findByRole("button", {
      name: /show action details/i,
    });
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    const actionContext = document.getElementById(
      "approval-action-context-card-approval-1",
    );
    expect(actionContext?.className).toContain("hidden");

    fireEvent.click(disclosure);

    expect(disclosure.getAttribute("aria-expanded")).toBe("true");
    expect(actionContext?.className).not.toContain("hidden");

    const approveButtons = screen.getAllByRole("button", { name: /approve/i });
    expect(
      approveButtons.some(
        (button) =>
          button.className.includes("h-11") &&
          button.className.includes("w-full"),
      ),
    ).toBe(true);
    const rejectButtons = screen.getAllByRole("button", { name: /reject/i });
    expect(
      rejectButtons.some(
        (button) =>
          button.className.includes("h-11") &&
          button.className.includes("w-full"),
      ),
    ).toBe(true);

    const redirect = screen.getByRole("button", { name: /redirect/i });
    expect(redirect.className).toContain("h-11");
    expect((redirect as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/redirect guidance/i), {
      target: { value: "Collect logs first." },
    });

    expect((redirect as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(redirect);

    await waitFor(() =>
      expect(apiMocks.redirectRequest).toHaveBeenCalledWith(
        "approval-1",
        "Collect logs first.",
      ),
    );
  });

  it("counts the parameters of the tool call, not the keys of the action record", async () => {
    // M1-169: a session's action holds tool_name, tool_parameters and often a
    // justification, so the page said "2 parameters" whatever the call held.
    const request = (id: string, action: Record<string, unknown>) => ({
      id,
      session_id: `session-${id}`,
      session_tier: 1,
      action,
      justification: null,
      status: "pending",
      resolution_note: null,
      requested_at: "2026-07-06T12:00:00Z",
      resolved_at: null,
      resolved_by: null,
      expires_at: "2026-07-06T12:10:00Z",
      extension_count: 0,
      extension_notified_at: null,
    });
    const items = [
      request("three", {
        tool_name: "restart_deployment",
        tool_parameters: { namespace: "bookinfo", deployment: "ratings-v1", wait: true },
        justification: "The pod is wedged.",
      }),
      request("one", {
        workflow_step: true,
        step_id: "s1",
        description: "",
        tool_name: "get_config",
        tool_parameters: { service: "ratings" },
        inputs: { service: "ratings" },
        safety_class: "safe",
        tier_override: null,
      }),
      request("none", { tool_name: "list_nodes", tool_parameters: {} }),
      // A session action the model wrote without a parameters object runs
      // with none, whatever else the record holds.
      request("null", { tool_name: "list_nodes", tool_parameters: null, justification: "why" }),
      request("absent", { tool_name: "list_nodes", justification: "why" }),
      request("list", { tool_name: "list_nodes", tool_parameters: ["a", "b"] }),
      // An older shape without tool_parameters keeps the count of its keys.
      request("legacy", { tool: "restart_pod", namespace: "prod", pod: "ratings-v1-7d9c" }),
    ];
    apiMocks.listApprovals.mockResolvedValue({ items, total: items.length });

    render(<ApprovalsPage />);

    // Each request is drawn twice: the phone card and the desktop row.
    expect(await screen.findAllByText("3 parameters")).toHaveLength(2);
    expect(screen.getAllByText("1 parameter")).toHaveLength(2);
    expect(screen.getAllByText("No parameters")).toHaveLength(8);
    expect(screen.getAllByText("2 parameters")).toHaveLength(2);
    expect(screen.queryByText("7 parameters")).toBeNull();
    expect(screen.queryByText("Review required")).toBeNull();
  });

  it("heads a request by the tool that runs, not by a key the model added", async () => {
    // M1-173: the tier check gates tool_name and the session runs it. The
    // model writes the record, so it can add "tool" and "service" beside it.
    const request = (id: string, action: Record<string, unknown>) => ({
      id,
      session_id: `session-${id}`,
      session_tier: 1,
      action,
      justification: null,
      status: "pending",
      resolution_note: null,
      requested_at: "2026-07-06T12:00:00Z",
      resolved_at: null,
      resolved_by: null,
      expires_at: "2026-07-06T12:10:00Z",
      extension_count: 0,
      extension_notified_at: null,
    });
    const items = [
      request("both", {
        tool_name: "delete_pod",
        tool: "get_pods",
        tool_parameters: { namespace: "bookinfo", pod: "ratings-v1-7d9c" },
        service: "read only check",
      }),
      request("blank", { tool_name: "", tool: "list_nodes", kind: "harmless read" }),
      request("target", { tool_name: "scale_deployment", target: "nothing at all" }),
      request("resource", { tool_name: "drain_node", resource: "no node" }),
      // A name of separators only title-cases to nothing.
      request("empty", { tool_name: "___" }),
      // A record in an older shape reads as before.
      request("legacy", { tool: "restart_pod", service: "checkout" }),
    ];
    apiMocks.listApprovals.mockResolvedValue({ items, total: items.length });

    render(<ApprovalsPage />);

    // Each request is drawn twice: the phone card and the desktop row.
    expect(await screen.findAllByText("Delete Pod")).toHaveLength(2);
    expect(screen.queryByText("Get Pods")).toBeNull();
    expect(screen.queryByText("Read Only Check")).toBeNull();
    expect(screen.getAllByText("Requested action")).toHaveLength(4);
    expect(screen.queryByText("List Nodes")).toBeNull();
    expect(screen.queryByText("Harmless Read")).toBeNull();
    expect(screen.getAllByText("Scale Deployment")).toHaveLength(2);
    expect(screen.queryByText("Nothing At All")).toBeNull();
    expect(screen.getAllByText("Drain Node")).toHaveLength(2);
    expect(screen.queryByText("No Node")).toBeNull();
    expect(screen.getAllByText("Restart Pod")).toHaveLength(2);
    expect(screen.getAllByText("Checkout")).toHaveLength(2);
  });

  // M1-172: the page asks for 100 requests of the chosen status, or of every
  // status under All statuses. What it counts as waiting is the server's
  // count of pending requests, not its own rows.
  const pendingRequest = (n: number, status = "pending") => ({
    ...LATER_ROUND_APPROVAL,
    id: `approval-${status}-${n}`,
    session_id: `session-${status}-${n}`,
    evidence: null,
    status,
  });

  it("counts every pending request in its heading, past the page", async () => {
    const items = Array.from({ length: 100 }, (_, n) => pendingRequest(n));
    apiMocks.listApprovals.mockResolvedValue({
      items,
      total: 130,
      status_counts: { pending: 130, approved: 4 },
    });

    render(<ApprovalsPage />);

    expect(await screen.findByText("130 total requests")).toBeTruthy();
    expect(screen.getByText("100 shown")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Approvals130");
  });

  it("says how many requests wait under another status filter", async () => {
    const counts = { pending: 3, approved: 2 };
    apiMocks.listApprovals.mockImplementation(async (params?: { status?: string }) => {
      const status = params?.status ?? "pending";
      const items = Array.from({ length: counts[status as keyof typeof counts] ?? 0 }, (_, n) =>
        pendingRequest(n, status),
      );
      return { items, total: items.length, status_counts: counts };
    });

    render(<ApprovalsPage />);
    expect(await screen.findByText("3 total requests")).toBeTruthy();
    expect(screen.queryByText(/waiting for review/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Approved/ }));

    // Wait for the approved rows: until they come, the page still holds
    // the pending ones it had.
    expect(await screen.findByText("2 total requests")).toBeTruthy();
    expect(screen.getByText("3 approvals waiting for review. Click to view.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Approvals3");
  });
});
