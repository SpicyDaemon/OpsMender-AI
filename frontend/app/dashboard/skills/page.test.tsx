/**
 * MCP Skills page (MCP Skill Studio) — title, New skill, Unassigned
 * assignment, and Markdown download.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: "u", username: "admin", role: "admin" } }),
}));
const toastSpies = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => toastSpies,
}));

const apiMocks = vi.hoisted(() => ({
  listSkills: vi.fn(),
  listMCPServers: vi.fn(),
  listIntegrationConnectors: vi.fn(),
  getSkillTemplate: vi.fn(),
  createSkill: vi.fn(),
  updateSkill: vi.fn(),
  deleteSkill: vi.fn(),
  cloneSkill: vi.fn(),
  importSkill: vi.fn(),
  discoverSkillTools: vi.fn(),
  generateSkill: vi.fn(),
  aiSuggestSkill: vi.fn(),
  validateSkill: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import SkillsPage from "@/app/dashboard/skills/page";

const SKILL = {
  id: "s1",
  name: "prod-skill",
  description: "Prod policy",
  mcp_server_id: null,
  integration_connector_id: null,
  assignment: "unassigned" as const,
  content_md: "# Tier 2 — Advisory Only\nNo actions allowed.",
  focus_areas: [],
  created_at: "2026-06-06T00:00:00Z",
  updated_at: "2026-06-06T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.listMCPServers.mockResolvedValue({ items: [], total: 0 });
  apiMocks.listIntegrationConnectors.mockResolvedValue({ items: [], total: 0 });
  apiMocks.listSkills.mockResolvedValue({ items: [SKILL], total: 1 });
  apiMocks.validateSkill.mockResolvedValue({
    valid: true,
    issues: [],
    operations: [],
  });
  apiMocks.getSkillTemplate.mockResolvedValue({
    name: "New MCP Skill (from template)",
    content_md:
      "default_tier: T2\nrequire_reversible: false\nallow_generic: true\n# template\n## Tier 0 — Autonomous\n## Tier 1 — Approval Required\n## Tier 2 — Advisory Only\n",
    template: "blank",
    templates: [
      {
        id: "blank",
        label: "Blank",
        description: "Generic starter.",
      },
      {
        id: "kubernetes",
        label: "Kubernetes / container ops",
        description: "Container operations.",
      },
    ],
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

  it("shows New skill and loads the 3-tier template into the editor", async () => {
    await renderPage();
    const btns = screen.getAllByRole("button", { name: /new skill/i });
    expect(btns.length).toBeGreaterThan(0);
    fireEvent.click(btns[0]);
    await waitFor(() => expect(apiMocks.getSkillTemplate).toHaveBeenCalled());
    // The modal opens with the template content (Tier sections present).
    await waitFor(() =>
      expect(screen.getByDisplayValue(/Tier 2 — Advisory Only/)).toBeTruthy(),
    );
    expect(screen.getByDisplayValue(/require_reversible: false/)).toBeTruthy();
  });

  it("shows an Unassigned badge for a draft skill", async () => {
    await renderPage();
    expect(screen.getAllByText(/unassigned/i).length).toBeGreaterThan(0);
  });

  it("surfaces the compatibility conversion notice after saving", async () => {
    apiMocks.createSkill.mockResolvedValue({
      ...SKILL,
      conversion_notice: "Converted to explicit tier policies. Review before use.",
    });
    await renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /^new skill$/i }));
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "converted-skill" },
    });
    fireEvent.click(await screen.findByRole("button", { name: /create skill/i }));

    await waitFor(() =>
      expect(toastSpies.info).toHaveBeenCalledWith(
        "Converted to explicit tier policies. Review before use.",
      ),
    );
  });

  it("Download action is present for every skill (incl. unassigned)", async () => {
    await renderPage();
    expect(
      screen.getAllByRole("button", { name: /download/i }).length,
    ).toBeGreaterThan(0);
  });

  it("warns about generic command tools + guide/enforce distinction in the editor", async () => {
    await renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /^new skill$/i }));
    await waitFor(() => expect(screen.getByLabelText("Assignment")).toBeTruthy());
    // High-risk generic-tool warning + the skills-guide / backend-enforces line.
    expect(screen.getByText(/Generic command tools/i)).toBeTruthy();
    expect(screen.getAllByText(/backend tier gate enforces/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/exact tool\/action identifiers/i)).toBeTruthy();
  });

  it("Skill Studio: discover MCP tools, generate a draft, and open the editor", async () => {
    apiMocks.listMCPServers.mockResolvedValue({
      items: [
        {
          id: "srv-1",
          name: "k8s-prod",
          transport: "stdio",
          command: "echo",
          args: [],
          env_vars: {},
          url: null,
          created_at: "2026-06-06T00:00:00Z",
          updated_at: "2026-06-06T00:00:00Z",
        },
      ],
      total: 1,
    });
    apiMocks.discoverSkillTools.mockResolvedValue({
      mcp_server_id: "srv-1",
      mcp_server_name: "k8s-prod",
      tools: [
        {
          name: "get_pods",
          description: "List pods",
          suggested_classification: "safe",
          generic: false,
          suggested_deny: false,
          needs_review: false,
          rationale: "read-only",
        },
        {
          name: "kubectl",
          description: "Run kubectl",
          suggested_classification: "destructive",
          generic: true,
          suggested_deny: true,
          needs_review: true,
          rationale: "arbitrary commands",
        },
      ],
    });
    apiMocks.generateSkill.mockResolvedValue({
      name: "k8s-prod skill",
      content_md:
        "---\nversion: \"1\"\n---\n# k8s-prod skill\n## Tier 0 — Autonomous\nGENERATED-CONTENT",
    });

    await renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: /generate from tools/i })[0]);

    // Pick the server and discover its tools.
    const select = await screen.findByLabelText("Tool source");
    await screen.findByRole("option", { name: /k8s-prod/i });
    fireEvent.change(select, { target: { value: "srv-1" } });
    fireEvent.click(await screen.findByRole("button", { name: /discover tools/i }));

    await waitFor(() =>
      expect(apiMocks.discoverSkillTools).toHaveBeenCalledWith("srv-1"),
    );
    // Discovered tools render with their suggestions.
    expect(await screen.findByText("get_pods")).toBeTruthy();
    expect(screen.getByText("kubectl")).toBeTruthy();
    expect(screen.getAllByText("generic").length).toBeGreaterThan(0);

    // Generate the draft and hand off to the editor. kubectl is flagged for
    // review, so confirm past the needs-review guard.
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(await screen.findByRole("button", { name: /generate draft/i }));
    await waitFor(() => expect(apiMocks.generateSkill).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByDisplayValue(/GENERATED-CONTENT/)).toBeTruthy(),
    );
    confirmSpy.mockRestore();
  });

  it("Skill Studio: AI assist applies suggestions and per-tier instructions", async () => {
    apiMocks.listMCPServers.mockResolvedValue({
      items: [
        {
          id: "srv-1",
          name: "k8s-prod",
          transport: "stdio",
          command: "echo",
          args: [],
          env_vars: {},
          url: null,
          created_at: "2026-06-06T00:00:00Z",
          updated_at: "2026-06-06T00:00:00Z",
        },
      ],
      total: 1,
    });
    apiMocks.discoverSkillTools.mockResolvedValue({
      mcp_server_id: "srv-1",
      mcp_server_name: "k8s-prod",
      tools: [
        {
          name: "get_pods",
          description: "List pods",
          suggested_classification: "safe",
          generic: false,
          suggested_deny: false,
          needs_review: false,
          rationale: "read-only",
        },
      ],
    });
    apiMocks.aiSuggestSkill.mockResolvedValue({
      tools: [
        {
          name: "get_pods",
          classification: "caution",
          deny: false,
          allow_generic: false,
          reversible: null,
          generic: false,
          needs_review: true,
          rationale: "model bumped it",
        },
      ],
      tier0_instructions: "AI-T0-GUIDANCE",
      tier1_instructions: "AI-T1",
      tier2_instructions: "AI-T2",
      environment: "production",
    });

    await renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: /generate from tools/i })[0]);
    const aiSelect = await screen.findByLabelText("Tool source");
    await screen.findByRole("option", { name: /k8s-prod/i });
    fireEvent.change(aiSelect, { target: { value: "srv-1" } });
    fireEvent.click(await screen.findByRole("button", { name: /discover tools/i }));
    await screen.findByText("get_pods");

    // Run AI assist.
    fireEvent.click(await screen.findByRole("button", { name: /ai assist/i }));
    await waitFor(() => expect(apiMocks.aiSuggestSkill).toHaveBeenCalled());

    // Classification select updated and per-tier instructions filled in.
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Classification for get_pods") as HTMLSelectElement)
          .value,
      ).toBe("caution"),
    );
    expect(screen.getByDisplayValue("AI-T0-GUIDANCE")).toBeTruthy();
  });

  async function openStudioWithTool(tool: Record<string, unknown>) {
    apiMocks.listMCPServers.mockResolvedValue({
      items: [
        {
          id: "srv-1",
          name: "k8s-prod",
          transport: "stdio",
          command: "echo",
          args: [],
          env_vars: {},
          url: null,
          created_at: "2026-06-06T00:00:00Z",
          updated_at: "2026-06-06T00:00:00Z",
        },
      ],
      total: 1,
    });
    apiMocks.discoverSkillTools.mockResolvedValue({
      mcp_server_id: "srv-1",
      mcp_server_name: "k8s-prod",
      tools: [tool],
    });
    await renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: /generate from tools/i })[0]);
    const select = await screen.findByLabelText("Tool source");
    // Wait for the server list to populate before selecting, otherwise the
    // change sets a value with no matching <option> and discovery never fires.
    await screen.findByRole("option", { name: /k8s-prod/i });
    fireEvent.change(select, { target: { value: "srv-1" } });
    fireEvent.click(await screen.findByRole("button", { name: /discover tools/i }));
    await screen.findByText(tool.name as string);
  }

  const CAUTION_TOOL = {
    name: "restart_service",
    description: "Roll a deployment",
    suggested_classification: "caution",
    generic: false,
    suggested_deny: false,
    needs_review: false,
    rationale: "reversible write",
  };

  it("Tier 0: shows reversible + inverse fields and blocks generate until provided", async () => {
    apiMocks.generateSkill.mockResolvedValue({
      name: "k8s-prod skill",
      content_md: "---\n---\n# x\nGENERATED",
    });
    await openStudioWithTool(CAUTION_TOOL);

    // Tier 0 fields are hidden until the tool is opted into autonomous run.
    expect(screen.queryByLabelText("Reversible restart_service")).toBeNull();
    fireEvent.click(await screen.findByLabelText("Allow restart_service at Tier 0"));
    expect(screen.getByLabelText("Reversible restart_service")).toBeTruthy();
    expect(
      screen.getByLabelText("Compensating inverse for restart_service"),
    ).toBeTruthy();

    // Generate is blocked while the inverse is missing.
    fireEvent.click(await screen.findByRole("button", { name: /generate draft/i }));
    await waitFor(() =>
      expect(screen.getByText(/Tier 0 \(autonomous\) actions need/i)).toBeTruthy(),
    );
    expect(apiMocks.generateSkill).not.toHaveBeenCalled();

    // Provide the inverse → generate succeeds and sends the safety metadata.
    fireEvent.change(
      screen.getByLabelText("Compensating inverse for restart_service"),
      { target: { value: "restart_service_previous" } },
    );
    fireEvent.click(await screen.findByRole("button", { name: /generate draft/i }));
    await waitFor(() => expect(apiMocks.generateSkill).toHaveBeenCalled());
    const payload = apiMocks.generateSkill.mock.calls[0][0];
    const op = payload.operations[0];
    expect(op.reversible).toBe(true);
    expect(op.compensating_inverse).toBe("restart_service_previous");
  });

  it("Tier 1/2 tools do not require Tier 0 safety metadata", async () => {
    apiMocks.generateSkill.mockResolvedValue({
      name: "s",
      content_md: "---\n---\n# x\nGENERATED",
    });
    await openStudioWithTool(CAUTION_TOOL);
    // Leave Tier 0 unticked (tool stays Tier 1/2) → generate succeeds, no metadata.
    fireEvent.click(await screen.findByRole("button", { name: /generate draft/i }));
    await waitFor(() => expect(apiMocks.generateSkill).toHaveBeenCalled());
    const op = apiMocks.generateSkill.mock.calls[0][0].operations[0];
    expect(op.reversible).toBeNull();
    expect(op.compensating_inverse).toBeNull();
  });

  it("AI assist maps reversible + inverse into the Tier 0 row", async () => {
    apiMocks.aiSuggestSkill.mockResolvedValue({
      tools: [
        {
          name: "restart_service",
          classification: "caution",
          deny: false,
          allow_generic: false,
          reversible: true,
          compensating_inverse: "restart_service_previous",
          generic: false,
          needs_review: false,
          rationale: "reversible with inverse",
        },
      ],
      tier0_instructions: "",
      tier1_instructions: "",
      tier2_instructions: "",
      environment: "production",
    });
    await openStudioWithTool(CAUTION_TOOL);
    fireEvent.click(await screen.findByRole("button", { name: /ai assist/i }));
    await waitFor(() => expect(apiMocks.aiSuggestSkill).toHaveBeenCalled());
    // The model's reversible+inverse turned on Tier 0 and filled the inverse.
    await waitFor(() =>
      expect(
        (
          screen.getByLabelText(
            "Compensating inverse for restart_service",
          ) as HTMLInputElement
        ).value,
      ).toBe("restart_service_previous"),
    );
  });

  it("Skill Studio: filter narrows the tool list", async () => {
    await openStudioWithTool(CAUTION_TOOL);
    // Discover only returns one tool in this helper, so add a second via a
    // dedicated discover mock for this test.
    expect(screen.getByText("restart_service")).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("Filter tools"), {
      target: { value: "zzz-no-match" },
    });
    expect(screen.queryByText("restart_service")).toBeNull();
    fireEvent.change(await screen.findByLabelText("Filter tools"), {
      target: { value: "restart" },
    });
    expect(screen.getByText("restart_service")).toBeTruthy();
  });

  it("Skill Studio: 'Deny all generic' denies generic tools", async () => {
    await openStudioWithTool({
      name: "kubectl",
      description: "run kubectl",
      suggested_classification: "destructive",
      generic: true,
      suggested_deny: false,
      needs_review: true,
      rationale: "arbitrary commands",
    });
    const denyBox = screen.getByLabelText("Deny kubectl") as HTMLInputElement;
    expect(denyBox.checked).toBe(false);
    fireEvent.click(await screen.findByRole("button", { name: /deny all generic/i }));
    expect(denyBox.checked).toBe(true);
  });

  it("Skill Studio: needs-review confirm guard before generate", async () => {
    apiMocks.generateSkill.mockResolvedValue({
      name: "s",
      content_md: "---\n---\n# x\nGENERATED",
    });
    // A flagged (needs_review) tool triggers a confirm before generation.
    await openStudioWithTool({
      name: "frobnicate",
      description: "unknown verb",
      suggested_classification: "caution",
      generic: false,
      suggested_deny: false,
      needs_review: true,
      rationale: "unrecognized",
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(await screen.findByRole("button", { name: /generate draft/i }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(apiMocks.generateSkill).not.toHaveBeenCalled();

    // Accepting the confirm proceeds.
    confirmSpy.mockReturnValue(true);
    fireEvent.click(await screen.findByRole("button", { name: /generate draft/i }));
    await waitFor(() => expect(apiMocks.generateSkill).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  it("create modal explains Unassigned drafts and defaults to unassigned", async () => {
    await renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /^new skill$/i }));
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

  it("loads a selected starter from the data-driven template library", async () => {
    apiMocks.getSkillTemplate
      .mockResolvedValueOnce({
        name: "New MCP Skill (from template)",
        content_md: "BLANK-STARTER",
        template: "blank",
        templates: [
          { id: "blank", label: "Blank", description: "Generic starter." },
          {
            id: "kubernetes",
            label: "Kubernetes / container ops",
            description: "Container operations.",
          },
        ],
      })
      .mockResolvedValueOnce({
        name: "Kubernetes / container ops",
        content_md: "KUBERNETES-STARTER",
        template: "kubernetes",
        templates: [],
      });
    await renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /^new skill$/i }));
    const template = await screen.findByLabelText("Starter template");
    fireEvent.change(template, { target: { value: "kubernetes" } });
    await waitFor(() =>
      expect(apiMocks.getSkillTemplate).toHaveBeenCalledWith("kubernetes"),
    );
    expect(await screen.findByDisplayValue("KUBERNETES-STARTER")).toBeTruthy();
  });

  it("shows line-anchored parser errors and blocks invalid saves", async () => {
    apiMocks.validateSkill.mockImplementation(async (content: string) =>
      content.includes("INVALID")
        ? {
            valid: false,
            issues: [
              {
                severity: "error",
                message: "Operation policy is invalid",
                line: 4,
              },
            ],
            operations: [],
          }
        : { valid: true, issues: [], operations: [] },
    );
    await renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
    fireEvent.change(await screen.findByLabelText("Skill content (SKILL.md)"), {
      target: { value: "INVALID" },
    });
    expect(await screen.findByText(/Line 4: Operation policy is invalid/)).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: /save changes/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("marks permission escalations in the before/after diff", async () => {
    apiMocks.validateSkill.mockImplementation(async (content: string) => ({
      valid: true,
      issues: [],
      operations: [
        {
          tool: "restart_service",
          classification: content.includes("ESCALATED")
            ? "destructive"
            : "safe",
          tiers: {
            T0: content.includes("ESCALATED") ? "autonomous" : "blocked",
            T1: "approval",
            T2: "advisory",
          },
        },
      ],
    }));
    await renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
    fireEvent.change(await screen.findByLabelText("Skill content (SKILL.md)"), {
      target: { value: "ESCALATED" },
    });
    expect(await screen.findByText("Permission changes before save")).toBeTruthy();
    expect(await screen.findByText("escalation")).toBeTruthy();
  });

  it("shows fail-closed coverage for an integration-bound skill", async () => {
    apiMocks.listIntegrationConnectors.mockResolvedValue({
      items: [
        {
          id: "connector-1",
          name: "Repository",
          kind: "github",
          is_enabled: true,
        },
      ],
      total: 1,
    });
    apiMocks.listSkills.mockResolvedValue({
      items: [
        {
          ...SKILL,
          assignment: "integration",
          integration_connector_id: "connector-1",
        },
      ],
      total: 1,
    });
    apiMocks.discoverSkillTools.mockResolvedValue({
      integration_connector_id: "connector-1",
      integration_connector_name: "Repository",
      tools: [
        {
          name: "integration__github__create_issue__connector1",
          description: "Create issue",
          suggested_classification: "caution",
          generic: false,
          suggested_deny: false,
          needs_review: false,
          rationale: "connector baseline",
        },
      ],
    });

    await renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
    expect(
      await screen.findByText(/Unclassified tools are denied at every tier/),
    ).toBeTruthy();
    expect(apiMocks.discoverSkillTools).toHaveBeenCalledWith({
      integration_connector_id: "connector-1",
    });
  });
});
