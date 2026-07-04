import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams("id=u-1"),
}));

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "u-admin", username: "admin", role: "admin", primary_org_id: "org-1" },
  }),
}));

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const apiMocks = vi.hoisted(() => ({
  getUser: vi.fn(),
  getUserDeletePreconditions: vi.fn(),
  mintPasswordReset: vi.fn(),
  setTemporaryPassword: vi.fn(),
  softDeleteUser: vi.fn(),
  updateUser: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import PersonDetailPage from "@/app/dashboard/people/detail/page";

beforeEach(() => {
  apiMocks.getUser.mockResolvedValue({
    id: "u-1",
    username: "ada",
    email: "ada@example.com",
    auth_source: "local",
    role: "operator",
    is_active: true,
    first_name: "Ada",
    last_name: "Lovelace",
    primary_org_id: "org-1",
    created_at: "2026-01-01T00:00:00Z",
    deleted_at: null,
  });
});

describe("People detail page", () => {
  it("uses the display name in the header and shows Joined in the summary", async () => {
    render(<PersonDetailPage />);

    expect(await screen.findByRole("heading", { name: "Ada Lovelace" })).toBeTruthy();
    expect(screen.getAllByText("ada@example.com").length).toBeGreaterThan(0);

    await waitFor(() => expect(screen.getByText("Joined")).toBeTruthy());
  });
});
