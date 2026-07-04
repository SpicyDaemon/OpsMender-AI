import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MCPSection } from "@/components/config/ConfigSections";
import type { MCPServerResponse, MCPServerStatusResponse } from "@/lib/types";

const SERVER: MCPServerResponse = {
  id: "mcp-1",
  name: "Kubernetes MCP",
  transport: "http",
  command: null,
  args: null,
  url: "http://mcp.local",
  env_vars: null,
  is_active: true,
  created_at: "2026-07-01T00:00:00Z",
  has_token: false,
  oauth_status: null,
};

const ERROR_STATUS: MCPServerStatusResponse = {
  server_id: "mcp-1",
  status: "error",
  last_successful_call_at: "2026-07-01T12:00:00Z",
  last_error: "Connection refused",
};

describe("MCP runtime status", () => {
  it("explains runtime errors with a focusable tooltip hint", () => {
    render(
      <MCPSection
        servers={[SERVER]}
        statuses={[ERROR_STATUS]}
        onReload={async () => {}}
        onStatusReload={async () => {}}
        canEdit
      />,
    );

    expect(
      screen.getAllByLabelText(/Runtime Error.*Connection refused.*Run Test for details/i)
        .length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("Runtime error").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Error: Connection refused").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Run Test for details.").length).toBeGreaterThan(0);
  });
});
