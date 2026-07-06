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
});
