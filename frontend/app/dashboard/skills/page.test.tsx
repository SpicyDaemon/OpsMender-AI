/**
 * MCP Skills page (MCP Skill Studio) — title, New from Template, Unassigned
 * assignment, and Markdown download.
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
  listSkills: vi.fn(),
  listMCPServers: vi.fn(),
  getSkillTemplate: vi.fn(),
  createSkill: vi.fn(),
  updateSkill: vi.fn(),
  deleteSkill: vi.fn(),
  cloneSkill: vi.fn(),
  importSkill: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import SkillsPage from "@/app/dashboard/skills/page";

const SKILL = {
  id: "s1",
  name: "prod-skill",
  description: "Prod policy",
  mcp_server_id: null,
  assignment: "unassigned" as const,
  content_md: "# Tier 2 — Advisory Only\nNo actions allowed.",
  focus_areas: [],
  created_at: "2026-06-06T00:00:00Z",
  updated_at: "2026-06-06T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.listMCPServers.mockResolvedValue({ items: [], total: 0 });
  apiMocks.listSkills.mockResolvedValue({ items: [SKILL], total: 1 });
  apiMocks.getSkillTemplate.mockResolvedValue({
    name: "New MCP Skill (from template)",
    content_md:
      "# template\n## Tier 0 — Autonomous\n## Tier 1 — Approval Required\n## Tier 2 — Advisory Only\n",
  });
});

async function renderPage() {
  render(<SkillsPage />);
  await waitFor(() => expect(apiMocks.listSkills).toHaveBeenCalled());
}

describe("MCP Skills page", () => {
  it("renders the MCP Skills / MCP Skill Studio title", async () => {
    await renderPage();
    expect(screen.getAllByText(/MCP Skills/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/MCP Skill Studio/i)).toBeTruthy();
  });

  it("shows New from Template and loads the 3-tier template into the editor", async () => {
    await renderPage();
    const btns = screen.getAllByRole("button", { name: /new from template/i });
    expect(btns.length).toBeGreaterThan(0);
    fireEvent.click(btns[0]);
    await waitFor(() => expect(apiMocks.getSkillTemplate).toHaveBeenCalled());
    // The modal opens with the template content (Tier sections present).
    await waitFor(() =>
      expect(screen.getByDisplayValue(/Tier 2 — Advisory Only/)).toBeTruthy(),
    );
  });

  it("shows an Unassigned badge for a draft skill", async () => {
    await renderPage();
    expect(screen.getAllByText(/unassigned/i).length).toBeGreaterThan(0);
  });

  it("Download action is present for every skill (incl. unassigned)", async () => {
    await renderPage();
    expect(
      screen.getAllByRole("button", { name: /download/i }).length,
    ).toBeGreaterThan(0);
  });

  it("warns about generic command tools + guide/enforce distinction in the editor", async () => {
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: /^new skill$/i }));
    await waitFor(() => expect(screen.getByLabelText("Assignment")).toBeTruthy());
    // High-risk generic-tool warning + the skills-guide / backend-enforces line.
    expect(screen.getByText(/Generic command tools/i)).toBeTruthy();
    expect(screen.getAllByText(/backend tier gate enforces/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/exact MCP tool\/action identifiers/i)).toBeTruthy();
  });

  it("create modal explains Unassigned drafts and defaults to unassigned", async () => {
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: /^new skill$/i }));
    // The assignment select defaults to "unassigned" for new skills.
    await waitFor(() =>
      expect(screen.getByLabelText("Assignment")).toBeTruthy(),
    );
    const select = screen.getByLabelText("Assignment") as HTMLSelectElement;
    expect(select.value).toBe("unassigned");
    // Helper copy explains drafts aren't injected into sessions.
    expect(
      screen.getByText(/never injected into AI sessions/i),
    ).toBeTruthy();
  });
});
