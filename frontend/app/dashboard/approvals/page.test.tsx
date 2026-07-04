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
}));

vi.mock("@/lib/api", () => apiMocks);

import ApprovalsPage from "@/app/dashboard/approvals/page";

beforeEach(() => {
  apiMocks.approveRequest.mockResolvedValue({});
  apiMocks.extendApprovalRequest.mockResolvedValue({});
  apiMocks.listApprovals.mockResolvedValue({ items: [], total: 0 });
  apiMocks.rejectRequest.mockResolvedValue({});
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
});
