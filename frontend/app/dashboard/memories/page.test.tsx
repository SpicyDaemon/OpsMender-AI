/**
 * Memories page — v1.2 review gate UI.
 *
 * AI-written memories arrive "pending" and must be approved before the AI can
 * recall them. The page surfaces a pending badge + banner and approve/reject
 * actions.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: "u", username: "admin", role: "admin" } }),
}));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const apiMocks = vi.hoisted(() => ({
  listMemories: vi.fn(),
  listServices: vi.fn(),
  createMemory: vi.fn(),
  updateMemory: vi.fn(),
  deleteMemory: vi.fn(),
  recordMemoryFeedback: vi.fn(),
  setMemoryHidden: vi.fn(),
  reviewMemory: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import MemoriesPage from "@/app/dashboard/memories/page";

function memory(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: "m1",
    org_id: "o1",
    service_id: null,
    source_incident_id: null,
    title: "Pending lesson",
    summary_md: "Roll the deployment.",
    tags: [],
    helpful_count: 0,
    unhelpful_count: 0,
    is_hidden: false,
    review_status: "pending",
    reviewed_by_user_id: null,
    reviewed_at: null,
    created_by_user_id: null,
    created_at: "2026-06-14T00:00:00Z",
    updated_at: "2026-06-14T00:00:00Z",
    last_used_at: null,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.listServices.mockResolvedValue({ items: [], total: 0 });
  apiMocks.listMemories.mockResolvedValue({ items: [memory()], total: 1 });
  apiMocks.reviewMemory.mockResolvedValue(
    memory({ review_status: "approved", reviewed_at: "2026-06-14T01:00:00Z" }),
  );
});

async function renderPage() {
  render(<MemoriesPage />);
  await waitFor(() => expect(apiMocks.listMemories).toHaveBeenCalled());
}

describe("Memories review gate", () => {
  it("shows a pending badge and review banner for AI-written memories", async () => {
    await renderPage();
    expect(screen.getAllByText(/Pending review/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/awaiting review/i)).toBeTruthy();
  });

  it("approves a pending memory via the row action", async () => {
    await renderPage();
    const approve = (await screen.findAllByTitle(/Approve/i))[0];
    fireEvent.click(approve);
    await waitFor(() =>
      expect(apiMocks.reviewMemory).toHaveBeenCalledWith("m1", "approved"),
    );
  });

  it("rejects a pending memory via the row action", async () => {
    apiMocks.reviewMemory.mockResolvedValue(
      memory({ review_status: "rejected" }),
    );
    await renderPage();
    const reject = (await screen.findAllByTitle(/Reject/i))[0];
    fireEvent.click(reject);
    await waitFor(() =>
      expect(apiMocks.reviewMemory).toHaveBeenCalledWith("m1", "rejected"),
    );
  });
});
