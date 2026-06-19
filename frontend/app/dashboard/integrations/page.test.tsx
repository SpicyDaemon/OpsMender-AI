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
    ],
    total: 1,
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
    await user.click(screen.getByRole("button", { name: "Create integration" }));
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
});
