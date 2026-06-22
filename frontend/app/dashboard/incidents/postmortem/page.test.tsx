/**
 * Postmortem page — v1.2 Phase 2 polish.
 *
 * Section-completeness checklist + "Save candidates to memory" handoff that
 * turns the Memory-candidates bullets into pending memories for review.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => ({ get: (k: string) => (k === "id" ? "inc-1" : null) }),
}));
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: "u", username: "admin", role: "admin" } }),
}));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const apiMocks = vi.hoisted(() => ({
  getIncident: vi.fn(),
  getIncidentPostmortem: vi.fn(),
  putIncidentPostmortem: vi.fn(),
  extractPostmortemMemoryCandidates: vi.fn(),
  draftIncidentPostmortemFromSessions: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import IncidentPostmortemPage from "@/app/dashboard/incidents/postmortem/page";

const POSTMORTEM_MD = [
  "## Summary",
  "Postgres ran out of disk.",
  "## Impact",
  "_Describe customer or system impact._",
  "## Memory candidates",
  "- Alert on disk > 80% for the primary.",
  "- Vacuum the audit table weekly.",
].join("\n");

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getIncident.mockResolvedValue({
    id: "inc-1",
    title: "DB outage",
    description: "disk full",
    severity: "high",
    status: "resolved",
    created_at: "2026-06-14T00:00:00Z",
    updated_at: "2026-06-14T01:00:00Z",
  });
  apiMocks.getIncidentPostmortem.mockResolvedValue({
    incident_id: "inc-1",
    postmortem_md: POSTMORTEM_MD,
    postmortem_updated_at: "2026-06-14T02:00:00Z",
    template: "## Summary\n",
  });
  apiMocks.extractPostmortemMemoryCandidates.mockResolvedValue({
    created: 2,
    skipped: 0,
    items: [],
  });
});

async function renderPage() {
  render(<IncidentPostmortemPage />);
  await waitFor(() => expect(apiMocks.getIncidentPostmortem).toHaveBeenCalled());
}

describe("Postmortem page Phase 2 polish", () => {
  it("shows a section completeness checklist", async () => {
    await renderPage();
    // Summary + Memory candidates are filled; Impact (placeholder) is not.
    await waitFor(() => expect(screen.getByText(/Recommended sections/)).toBeTruthy());
    expect(screen.getByText(/Recommended sections \(\d+\/7\)/)).toBeTruthy();
    expect(screen.getAllByLabelText("filled").length).toBeGreaterThan(0);
  });

  it("drafts from AI sessions via the backend and loads it into the editor", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    apiMocks.draftIncidentPostmortemFromSessions.mockResolvedValue({
      incident_id: "inc-1",
      draft: "## Summary\nDrafted from the session trail.\n## Root cause\nOOMKilled",
      source_session_ids: ["sess-1"],
    });
    await renderPage();
    const btn = await screen.findByRole("button", { name: /Draft from sessions/i });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(apiMocks.draftIncidentPostmortemFromSessions).toHaveBeenCalledWith(
        "inc-1",
      ),
    );
    await waitFor(() =>
      expect(screen.getByDisplayValue(/Drafted from the session trail/)).toBeTruthy(),
    );
  });

  it("offers a 'Save N to memory' button and calls the handoff", async () => {
    await renderPage();
    const btn = await screen.findByRole("button", { name: /Save 2 to memory/i });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(apiMocks.extractPostmortemMemoryCandidates).toHaveBeenCalledWith(
        "inc-1",
      ),
    );
  });

  it("hides the candidates button when there are none", async () => {
    apiMocks.getIncidentPostmortem.mockResolvedValue({
      incident_id: "inc-1",
      postmortem_md: "## Summary\nNo candidates here.",
      postmortem_updated_at: "2026-06-14T02:00:00Z",
      template: "## Summary\n",
    });
    await renderPage();
    await waitFor(() => expect(screen.getByText(/Recommended sections/)).toBeTruthy());
    expect(screen.queryByRole("button", { name: /to memory/i })).toBeNull();
  });
});
