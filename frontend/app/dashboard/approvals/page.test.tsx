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

describe("Approvals page", () => {
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
});
