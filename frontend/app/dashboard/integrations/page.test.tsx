import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  createIntegrationConnector: vi.fn(),
  deleteIntegrationConnector: vi.fn(),
  listIntegrationConnectors: vi.fn(),
  listIntegrationKinds: vi.fn(),
  testIntegrationConnector: vi.fn(),
  updateIntegrationConnector: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import IntegrationsPage from "./page";

const connector = {
  id: "11111111-1111-1111-1111-111111111111",
  org_id: "00000000-0000-0000-0000-000000000000",
  kind: "custom",
  name: "Status API",
  base_url: "https://status.example.test",
  auth_type: "pat",
  auth_keys: ["token"],
  has_auth: true,
  config: {},
  is_enabled: true,
  status: "healthy",
  last_checked_at: "2026-06-19T12:00:00Z",
  last_error: null,
  created_at: "2026-06-19T11:00:00Z",
  updated_at: "2026-06-19T12:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.listIntegrationKinds.mockResolvedValue({
    items: [
      {
        kind: "github",
        label: "GitHub",
        supports_base_url: true,
        auth_types: ["pat", "app"],
        adapter_available: true,
        capabilities: [
          {
            action: "get_file",
            description: "Read file",
            classification: "safe",
            mutating: false,
            always_requires_approval: false,
          },
          {
            action: "create_pull_request",
            description: "Create pull request",
            classification: "caution",
            mutating: true,
            always_requires_approval: false,
          },
        ],
      },
      {
        kind: "jira",
        label: "Jira",
        supports_base_url: true,
        auth_types: ["pat", "oauth", "basic"],
        adapter_available: true,
        capabilities: [],
      },
      {
        kind: "custom",
        label: "Custom HTTP",
        supports_base_url: true,
        auth_types: ["none", "pat"],
        adapter_available: true,
        capabilities: [
          {
            action: "test_connection",
            description: "Probe endpoint",
            classification: "safe",
            mutating: false,
            always_requires_approval: false,
          },
        ],
      },
      {
        kind: "azure_devops",
        label: "Azure DevOps",
        supports_base_url: true,
        auth_types: ["pat", "oauth"],
        adapter_available: true,
        capabilities: [
          {
            action: "create_work_item",
            description: "Create work item",
            classification: "caution",
            mutating: true,
            always_requires_approval: false,
          },
        ],
      },
      {
        kind: "jenkins",
        label: "Jenkins",
        supports_base_url: true,
        auth_types: ["basic", "pat"],
        adapter_available: true,
        capabilities: [
          {
            action: "trigger_build",
            description: "Trigger build",
            classification: "caution",
            mutating: true,
            always_requires_approval: false,
          },
        ],
      },
      {
        kind: "terraform_cloud",
        label: "Terraform Cloud",
        supports_base_url: true,
        auth_types: ["api_key"],
        adapter_available: true,
        capabilities: [
          {
            action: "plan",
            description: "Queue plan",
            classification: "destructive",
            mutating: true,
            always_requires_approval: true,
          },
        ],
      },
      {
        kind: "google_docs",
        label: "Google Docs",
        supports_base_url: false,
        auth_types: ["oauth", "custom"],
        adapter_available: true,
        capabilities: [
          {
            action: "read_doc",
            description: "Read document",
            classification: "safe",
            mutating: false,
            always_requires_approval: false,
          },
          {
            action: "export_doc",
            description: "Export document",
            classification: "safe",
            mutating: false,
            always_requires_approval: false,
          },
        ],
      },
    ],
    total: 2,
  });
  apiMocks.listIntegrationConnectors.mockResolvedValue({
    items: [connector],
    total: 1,
  });
  apiMocks.createIntegrationConnector.mockResolvedValue(connector);
  apiMocks.updateIntegrationConnector.mockResolvedValue(connector);
  apiMocks.testIntegrationConnector.mockResolvedValue({
    success: true,
    detail: "Connection successful.",
    latency_ms: 10,
  });
});

describe("Integrations page", () => {
  it("shows connector health without exposing credential values", async () => {
    render(<IntegrationsPage />);
    expect(await screen.findByText("Status API")).toBeTruthy();
    expect(screen.getByText("healthy")).toBeTruthy();
    expect(screen.getByText(/Credentials configured \(token\)/)).toBeTruthy();
    expect(screen.queryByText("top-secret")).toBeNull();
  });

  it("creates a connector from JSON credentials and configuration", async () => {
    apiMocks.listIntegrationConnectors.mockResolvedValueOnce({
      items: [],
      total: 0,
    });
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await user.type(screen.getByLabelText("Name"), "Build API");
    await user.type(
      screen.getByLabelText("Base URL"),
      "https://build.example.test",
    );
    fireEvent.change(screen.getByLabelText("Credentials JSON"), {
      target: { value: '{"token":"secret"}' },
    });
    fireEvent.change(screen.getByLabelText("Configuration JSON"), {
      target: { value: '{"health_path":"/ready"}' },
    });
    await user.click(
      screen.getByRole("button", { name: "Create integration" }),
    );
    await waitFor(() =>
      expect(apiMocks.createIntegrationConnector).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Build API",
          base_url: "https://build.example.test",
          auth: { token: "secret" },
          config: { health_path: "/ready" },
        }),
      ),
    );
  });

  it("shows source-control auth guidance and capability policy", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await user.selectOptions(screen.getByLabelText("Kind"), "github");
    expect(
      screen.getByText(/Hosted default: https:\/\/api.github.com/),
    ).toBeTruthy();
    expect(screen.getByText(/App: \{"app_id"/)).toBeTruthy();
    expect(
      screen.getByText(/create_pull_request · approval-gated write/),
    ).toBeTruthy();
  });

  it("shows provider-specific setup guidance for phase-four adapters", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await user.selectOptions(screen.getByLabelText("Kind"), "azure_devops");
    expect(
      screen.getByText(/collection URL for a self-hosted deployment/),
    ).toBeTruthy();
    expect(screen.getByText(/"organization":"acme"/)).toBeTruthy();
    expect(
      screen.getByText(/create_work_item · approval-gated write/),
    ).toBeTruthy();
  });

  it("shows CI/CD authentication and project guidance", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await user.selectOptions(screen.getByLabelText("Kind"), "jenkins");
    expect(screen.getByText(/Jenkins controller URL/)).toBeTruthy();
    expect(screen.getByText(/"api_token"/)).toBeTruthy();
    expect(screen.getByText(/"job":"folder\/service"/)).toBeTruthy();
    expect(
      screen.getByText(/trigger_build · approval-gated write/),
    ).toBeTruthy();
  });

  it("shows infrastructure automation approval and setup guidance", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await user.selectOptions(screen.getByLabelText("Kind"), "terraform_cloud");
    expect(screen.getByText(/Terraform Enterprise API v2 base/)).toBeTruthy();
    expect(screen.getByText(/"workspace_id":"ws-/)).toBeTruthy();
    expect(screen.getByText(/plan · always approval/)).toBeTruthy();
  });

  it("shows Google Docs OAuth and service-account guidance", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await user.selectOptions(screen.getByLabelText("Kind"), "google_docs");
    expect(screen.getByText(/Google Docs and Drive APIs/)).toBeTruthy();
    expect(screen.getByText(/Service account \(Custom\)/)).toBeTruthy();
    expect(screen.getByText(/domain-wide delegation/)).toBeTruthy();
    expect(screen.getByText(/read_doc · read/)).toBeTruthy();
  });

  it("configures Jira status mapping and shows the signed webhook URL", async () => {
    const jiraConnector = {
      ...connector,
      kind: "jira",
      name: "Jira Tickets",
      base_url: "https://tickets.example.test",
      config: {
        ticket_sync_enabled: true,
        status_map: {
          open: "Backlog",
          in_progress: "Working",
          resolved: "Complete",
        },
      },
    };
    apiMocks.listIntegrationConnectors.mockResolvedValue({
      items: [jiraConnector],
      total: 1,
    });
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    expect(await screen.findByText("Bi-directional ticket sync")).toBeTruthy();
    expect(
      screen.getByText(
        `/webhooks/ticket-sync/${jiraConnector.id}`,
      ),
    ).toBeTruthy();
    expect((screen.getByLabelText("resolved") as HTMLInputElement).value).toBe(
      "Complete",
    );
    await user.clear(screen.getByLabelText("resolved"));
    await user.type(screen.getByLabelText("resolved"), "Closed");
    await user.click(screen.getByRole("button", { name: "Save sync settings" }));
    await waitFor(() =>
      expect(apiMocks.updateIntegrationConnector).toHaveBeenCalledWith(
        jiraConnector.id,
        expect.objectContaining({
          config: expect.objectContaining({
            ticket_sync_enabled: true,
            status_map: expect.objectContaining({ resolved: "Closed" }),
          }),
        }),
      ),
    );
  });
});
