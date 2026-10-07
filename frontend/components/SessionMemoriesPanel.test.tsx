import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getSessionMemoriesUsed: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import { SessionMemoriesPanel } from "@/components/SessionMemoriesPanel";

describe("SessionMemoriesPanel", () => {
  it("lists the memories a session used, without feedback buttons", async () => {
    apiMocks.getSessionMemoriesUsed.mockResolvedValue({
      items: [
        {
          memory: {
            id: "m1",
            org_id: "org-1",
            service_id: null,
            source_incident_id: null,
            title: "Restart the cache first",
            summary_md: "It clears the stale keys.",
            tags: [],
            can_edit: true,
            can_delete: true,
            created_by_user_id: null,
            created_at: "2026-10-01T00:00:00Z",
            updated_at: "2026-10-01T00:00:00Z",
            last_used_at: null,
          },
          surfaced_at: "2026-10-02T00:00:00Z",
          score: 1.5,
        },
      ],
      total: 1,
    });
    render(<SessionMemoriesPanel sessionId="s1" defaultOpen />);
    await waitFor(() => expect(screen.getByText("Restart the cache first")).toBeTruthy());
    expect(apiMocks.getSessionMemoriesUsed).toHaveBeenCalledWith("s1");
    // Memory feedback was removed (M1-20).
    expect(screen.queryByTitle("Helpful")).toBeNull();
    expect(screen.queryByTitle("Not helpful")).toBeNull();
  });
});
