import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowSettingsSection } from "@/components/config/ConfigSections";

const apiMocks = vi.hoisted(() => ({
  getWorkflowSettings: vi.fn(),
  updateWorkflowSettings: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getWorkflowSettings: apiMocks.getWorkflowSettings,
  updateWorkflowSettings: apiMocks.updateWorkflowSettings,
}));

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getWorkflowSettings.mockResolvedValue({
    workflow_enabled: true,
    node_order: [
      "recall",
      "observe",
      "diagnose",
      "plan",
      "tier_gate",
      "execute",
      "summarize",
      "remember",
    ],
  });
  apiMocks.updateWorkflowSettings.mockImplementation(async (body) => body);
});

describe("WorkflowSettingsSection", () => {
  it("loads, reorders, and saves the workspace session workflow", async () => {
    const user = userEvent.setup();
    render(<WorkflowSettingsSection canEdit />);

    await screen.findByText("Session workflow");
    expect(
      (screen.getByLabelText("Workflow enabled") as HTMLInputElement).checked,
    ).toBe(true);

    await user.click(screen.getByLabelText("Move Diagnose earlier"));
    await user.click(screen.getByLabelText("Workflow enabled"));
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(apiMocks.updateWorkflowSettings).toHaveBeenCalledWith({
        workflow_enabled: false,
        node_order: [
          "recall",
          "diagnose",
          "observe",
          "plan",
          "tier_gate",
          "execute",
          "summarize",
          "remember",
        ],
      }),
    );
  });
});
