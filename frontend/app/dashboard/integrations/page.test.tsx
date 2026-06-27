import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The Kind picker is a custom IconSelect (a button + listbox), not a native
// <select>, so drive it by opening it and clicking the option by data-value.
async function pickKind(
  user: ReturnType<typeof userEvent.setup>,
  value: string,
) {
  await user.click(screen.getByLabelText("Kind"));
  const target = document.querySelector(`[role="option"][data-value="${value}"]`);
  if (!target) throw new Error(`Kind option not found: ${value}`);
  await user.click(target as HTMLElement);
}

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

function field(
  name: string,
  label: string,
  group: "credentials" | "config",
  options: {
    kind?: "text" | "secret" | "url" | "number" | "select" | "textarea";
    required?: boolean;
    default?: unknown;
    placeholder?: string;
    choices?: Array<{ value: string; label: string }>;
  } = {},
) {
  return {
    name,
    label,
    kind: options.kind ?? "text",
    group,
    required: options.required ?? false,
    helper: null,
    placeholder: options.placeholder ?? null,
    doc_url: null,
    options: options.choices ?? [],
    default: options.default ?? null,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.listIntegrationKinds.mockResolvedValue({
    items: [
      {
        kind: "github",
        label: "GitHub",
        supports_base_url: true,
        base_url_helper:
          "Hosted default: https://api.github.com. For Enterprise Server, enter its API base or instance root.",
        base_url_placeholder: "https://api.github.com",
        auth_types: ["pat", "app"],
        adapter_available: true,
        credential_fields: {
          pat: [
            field("token", "Personal access token", "credentials", {
              kind: "secret",
              required: true,
            }),
          ],
          app: [
            field("app_id", "App ID", "credentials", { required: true }),
            field("installation_id", "Installation ID", "credentials", {
              required: true,
            }),
            field("private_key", "Private key", "credentials", {
              kind: "textarea",
              required: true,
            }),
          ],
        },
        config_fields: [
          field("owner", "Default owner", "config"),
          field("repo", "Default repository", "config"),
          field("api_version", "API version", "config", {
            default: "2022-11-28",
          }),
        ],
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
        base_url_helper: "Required: your Jira Cloud site URL.",
        base_url_placeholder: "https://acme.atlassian.net",
        auth_types: ["pat", "oauth", "basic"],
        adapter_available: true,
        credential_fields: {
          pat: [
            field("email", "Atlassian account email", "credentials"),
            field("api_token", "API token", "credentials", {
              kind: "secret",
              required: true,
            }),
          ],
        },
        config_fields: [
          field("project_key", "Default project key", "config"),
          field("ticket_sync_enabled", "Ticket sync", "config", {
            kind: "select",
            default: false,
            choices: [
              { value: "false", label: "Disabled" },
              { value: "true", label: "Enabled" },
            ],
          }),
        ],
        capabilities: [],
      },
      {
        kind: "custom",
        label: "Custom HTTP",
        supports_base_url: true,
        base_url_helper: "Required: the HTTP endpoint root.",
        base_url_placeholder: "https://service.example.com",
        auth_types: ["none", "pat"],
        adapter_available: true,
        credential_fields: {
          none: [],
          pat: [
            field("token", "Token", "credentials", {
              kind: "secret",
              required: true,
            }),
          ],
        },
        config_fields: [
          field("headers", "Request headers", "config", {
            kind: "textarea",
            default: {},
          }),
          field("health_path", "Health path", "config", {
            placeholder: "/ready",
          }),
        ],
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
        base_url_helper:
          "Leave blank for Azure DevOps Services, or enter the collection URL for a self-hosted deployment.",
        base_url_placeholder: "https://dev.azure.com/acme",
        auth_types: ["pat", "oauth"],
        adapter_available: true,
        credential_fields: {
          pat: [
            field("token", "Token", "credentials", {
              kind: "secret",
              required: true,
            }),
          ],
        },
        config_fields: [
          field("organization", "Organization", "config", { required: true }),
          field("project", "Default project", "config"),
          field("repository", "Default repository", "config"),
        ],
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
        base_url_helper: "Required: the Jenkins controller URL.",
        base_url_placeholder: "https://jenkins.example.com",
        auth_types: ["basic", "pat"],
        adapter_available: true,
        credential_fields: {
          basic: [
            field("username", "Username", "credentials", { required: true }),
            field("password", "Password", "credentials", {
              kind: "secret",
              required: true,
            }),
          ],
          pat: [
            field("username", "Username", "credentials", { required: true }),
            field("api_token", "API token", "credentials", {
              kind: "secret",
              required: true,
            }),
          ],
        },
        config_fields: [field("job", "Default job", "config")],
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
        base_url_helper:
          "Uses https://app.terraform.io/api/v2 by default. Enter a Terraform Enterprise API v2 base when self-hosted.",
        base_url_placeholder: "https://app.terraform.io/api/v2",
        auth_types: ["api_key"],
        adapter_available: true,
        credential_fields: {
          api_key: [
            field("api_key", "API key", "credentials", {
              kind: "secret",
              required: true,
            }),
          ],
        },
        config_fields: [
          field("organization", "Organization", "config", { required: true }),
          field("workspace_id", "Default workspace ID", "config"),
        ],
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
        base_url_helper: null,
        base_url_placeholder: null,
        auth_types: ["oauth", "custom"],
        adapter_available: true,
        credential_fields: {
          oauth: [
            field("access_token", "Access token", "credentials", {
              kind: "secret",
              required: true,
            }),
          ],
          custom: [
            field("client_email", "Service-account email", "credentials", {
              required: true,
            }),
            field("private_key", "Private key", "credentials", {
              kind: "textarea",
              required: true,
            }),
          ],
        },
        config_fields: [],
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

  it("creates a connector from structured credential and config fields", async () => {
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
    await user.type(screen.getByLabelText("Token"), "secret");
    await user.type(screen.getByLabelText("Health path"), "/ready");
    await user.click(
      screen.getByRole("button", { name: "Create integration" }),
    );
    await waitFor(() =>
      expect(apiMocks.createIntegrationConnector).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Build API",
          base_url: "https://build.example.test",
          auth: { token: "secret" },
          config: expect.objectContaining({ health_path: "/ready" }),
        }),
      ),
    );
  });

  it("shows source-control auth guidance and capability policy", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await pickKind(user, "github");
    expect(
      screen.getByText(/Hosted default: https:\/\/api.github.com/),
    ).toBeTruthy();
    expect(screen.getByLabelText("Personal access token")).toBeTruthy();
    expect(screen.getByLabelText("Default owner")).toBeTruthy();
    await user.selectOptions(screen.getByLabelText("Authentication"), "app");
    expect(screen.getByLabelText("App ID")).toBeTruthy();
    expect(
      screen.getByText(/create_pull_request · approval-gated write/),
    ).toBeTruthy();
  });

  it("shows provider-specific setup guidance for phase-four adapters", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await pickKind(user, "azure_devops");
    expect(
      screen.getByText(/collection URL for a self-hosted deployment/),
    ).toBeTruthy();
    expect(screen.getByLabelText("Organization")).toBeTruthy();
    expect(screen.getByLabelText("Default repository")).toBeTruthy();
    expect(
      screen.getByText(/create_work_item · approval-gated write/),
    ).toBeTruthy();
  });

  it("shows CI/CD authentication and project guidance", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await pickKind(user, "jenkins");
    expect(screen.getByText(/Jenkins controller URL/)).toBeTruthy();
    expect(screen.getByLabelText("Username")).toBeTruthy();
    expect(screen.getByLabelText("Password")).toBeTruthy();
    expect(screen.getByLabelText("Default job")).toBeTruthy();
    expect(
      screen.getByText(/trigger_build · approval-gated write/),
    ).toBeTruthy();
  });

  it("shows infrastructure automation approval and setup guidance", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await pickKind(user, "terraform_cloud");
    expect(screen.getByText(/Terraform Enterprise API v2 base/)).toBeTruthy();
    expect(screen.getByLabelText("API key")).toBeTruthy();
    expect(screen.getByLabelText("Default workspace ID")).toBeTruthy();
    expect(screen.getByText(/plan · always approval/)).toBeTruthy();
  });

  it("shows Google Docs OAuth and service-account guidance", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByRole("heading", { name: "Add integration" });
    await pickKind(user, "google_docs");
    expect(screen.queryByLabelText("Base URL")).toBeNull();
    expect(screen.getByLabelText("Access token")).toBeTruthy();
    await user.selectOptions(screen.getByLabelText("Authentication"), "custom");
    expect(screen.getByLabelText("Service-account email")).toBeTruthy();
    expect(screen.getByLabelText("Private key")).toBeTruthy();
    expect(screen.getByText(/read_doc · read/)).toBeTruthy();
  });

  it("keeps saved credentials when their structured fields stay blank", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    expect(await screen.findByText("Status API")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const token = screen.getByLabelText("Token") as HTMLInputElement;
    expect(token.value).toBe("");
    expect(token.placeholder).toMatch(/Saved/);
    await user.click(screen.getByRole("button", { name: "Save integration" }));
    await waitFor(() =>
      expect(apiMocks.updateIntegrationConnector).toHaveBeenCalledWith(
        connector.id,
        expect.not.objectContaining({ auth: expect.anything() }),
      ),
    );
  });

  it("hydrates and round-trips additional credential and config variables", async () => {
    const legacyConnector = {
      ...connector,
      auth_keys: ["legacy_secret", "token"],
      config: {
        health_path: "/old",
        legacy_timeout: 30,
        legacy_empty: null,
      },
    };
    apiMocks.listIntegrationConnectors.mockResolvedValue({
      items: [legacyConnector],
      total: 1,
    });
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    expect(await screen.findByText("Status API")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    // The single merged list lists secret (credential) extras first, then
    // plaintext config extras.
    expect(
      (screen.getByLabelText("Additional variable key 1") as HTMLInputElement)
        .value,
    ).toBe("legacy_secret");
    expect(
      (screen.getByLabelText("Additional variable secret 1") as HTMLInputElement)
        .checked,
    ).toBe(true);
    expect(
      (
        screen.getByLabelText("Additional variable value 1") as HTMLInputElement
      ).placeholder,
    ).toMatch(/Saved/);
    expect(
      (screen.getByLabelText("Additional variable key 2") as HTMLInputElement)
        .value,
    ).toBe("legacy_timeout");
    expect(
      (screen.getByLabelText("Additional variable value 2") as HTMLInputElement)
        .value,
    ).toBe("30");
    expect(
      (screen.getByLabelText("Additional variable secret 2") as HTMLInputElement)
        .checked,
    ).toBe(false);
    expect(
      (screen.getByLabelText("Additional variable key 3") as HTMLInputElement)
        .value,
    ).toBe("legacy_empty");

    await user.click(screen.getByRole("button", { name: "Save integration" }));
    await waitFor(() =>
      expect(apiMocks.updateIntegrationConnector).toHaveBeenCalledWith(
        legacyConnector.id,
        expect.objectContaining({
          config: expect.objectContaining({
            health_path: "/old",
            legacy_timeout: 30,
            legacy_empty: null,
          }),
        }),
      ),
    );
    const payload = apiMocks.updateIntegrationConnector.mock.calls.at(-1)?.[1];
    expect(payload.auth).toBeUndefined();
  });

  it("merges new additional variables and explicitly removes saved secrets", async () => {
    const legacyConnector = {
      ...connector,
      auth_keys: ["legacy_secret", "token"],
      config: { legacy_timeout: 30 },
    };
    apiMocks.listIntegrationConnectors.mockResolvedValue({
      items: [legacyConnector],
      total: 1,
    });
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    expect(await screen.findByText("Status API")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    // Remove the saved secret row (legacy_secret); legacy_timeout shifts up.
    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    // Re-add legacy_secret as a Secret variable and add a plaintext one.
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(
      screen.getByLabelText("Additional variable key 2"),
      "legacy_secret",
    );
    await user.click(screen.getByLabelText("Additional variable secret 2"));
    await user.type(
      screen.getByLabelText("Additional variable value 2"),
      "secret-2",
    );
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(
      screen.getByLabelText("Additional variable key 3"),
      "region",
    );
    await user.type(
      screen.getByLabelText("Additional variable value 3"),
      "us-east-1",
    );
    await user.click(screen.getByRole("button", { name: "Save integration" }));

    await waitFor(() =>
      expect(apiMocks.updateIntegrationConnector).toHaveBeenCalledWith(
        legacyConnector.id,
        expect.objectContaining({
          auth: {
            legacy_secret: "secret-2",
          },
          config: expect.objectContaining({
            legacy_timeout: 30,
            region: "us-east-1",
          }),
        }),
      ),
    );
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
