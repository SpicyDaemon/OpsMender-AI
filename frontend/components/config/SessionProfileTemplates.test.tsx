/**
 * Session Profile templates (v1.2 Phase 3) — "New from template" prefills the
 * editor with a built-in preset's name, description, and node order.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({
  listSessionProfileTemplates: vi.fn(),
  createWorkflowProfile: vi.fn(),
  updateWorkflowProfile: vi.fn(),
  deleteWorkflowProfile: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import { WorkflowProfileSection } from "@/components/config/ConfigSections";

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.listSessionProfileTemplates.mockResolvedValue({
    items: [
      {
        key: "read_only_investigation",
        name: "Read-only Investigation",
        description: "Investigate and diagnose only — no changes.",
        node_order: ["observe", "diagnose", "summarize"],
      },
      {
        key: "standard_assisted_response",
        name: "Standard Assisted Response",
        description: "Full guided response.",
        node_order: [
          "observe",
          "diagnose",
          "plan",
          "tier_gate",
          "execute",
          "verify",
          "summarize",
        ],
      },
    ],
    total: 2,
  });
});

describe("Session Profile templates", () => {
  it("offers a 'New from template' picker and prefills the editor", async () => {
    render(
      <WorkflowProfileSection profiles={[]} onReload={async () => {}} canEdit />,
    );
    // Templates load on mount.
    const select = await screen.findByLabelText("New from template");
    expect(
      screen.getByRole("option", { name: "Read-only Investigation" }),
    ).toBeTruthy();

    fireEvent.change(select, { target: { value: "read_only_investigation" } });

    // The editor opens prefilled with the template's name + node order.
    await waitFor(() =>
      expect(screen.getByDisplayValue("Read-only Investigation")).toBeTruthy(),
    );
    // Read-only Investigation has no execute phase.
    expect(screen.queryByText("Execute")).toBeNull();
    expect(screen.getAllByText("Observe").length).toBeGreaterThan(0);
  });

  it("does not show the template picker without edit permission", async () => {
    render(
      <WorkflowProfileSection
        profiles={[]}
        onReload={async () => {}}
        canEdit={false}
      />,
    );
    // No fetch, no picker.
    expect(screen.queryByLabelText("New from template")).toBeNull();
  });
});
