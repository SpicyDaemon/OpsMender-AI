import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  ServiceToolsetCell,
  ToolSourceOverlapNote,
  toolSourceOverlapSummary,
} from "@/components/paging/ToolSourceOverlap";
import type { ServiceResponse, ToolSourceOverlap } from "@/lib/types";

const OVERLAP: ToolSourceOverlap = {
  kind: "jira",
  connector_id: "c-jira",
  connector_name: "Jira DOT",
  mcp_server_id: "m-atlassian",
  mcp_server_name: "atlassian",
  matched_term: "atlassian",
};

function service(overrides: Partial<ServiceResponse> = {}): ServiceResponse {
  return {
    id: "s1",
    team_id: "t1",
    name: "checkout",
    slug: "checkout",
    description: null,
    priority: "P1",
    alert_grouping: "inherit",
    mcp_server_ids: [],
    model_config_ids: [],
    allowed_integration_connector_ids: [],
    integration_action_overrides: {},
    intake_url: null,
    external_refs: null,
    is_active: true,
    created_at: new Date().toISOString(),
    ...overrides,
  } as ServiceResponse;
}

describe("ServiceToolsetCell", () => {
  it("keeps the existing labels and shows no warning without overlaps", () => {
    const { rerender } = render(
      <ServiceToolsetCell service={service({ mcp_server_ids: ["m1"] })} />,
    );
    expect(screen.getByText("MCP covered")).toBeTruthy();
    expect(screen.queryByText("Overlapping tool sources")).toBeNull();

    rerender(
      <ServiceToolsetCell
        service={service({ allowed_integration_connector_ids: ["c1"] })}
      />,
    );
    expect(screen.getByText(/Integrations are covering/)).toBeTruthy();

    rerender(<ServiceToolsetCell service={service()} />);
    expect(screen.getByText("Advisory only")).toBeTruthy();
    expect(screen.queryByText("Overlapping tool sources")).toBeNull();
  });

  it("treats an empty overlap list like a missing one", () => {
    render(
      <ServiceToolsetCell
        service={service({ mcp_server_ids: ["m1"], tool_source_overlaps: [] })}
      />,
    );
    expect(screen.queryByText("Overlapping tool sources")).toBeNull();
  });

  it("flags overlapping sources with a descriptive tooltip", () => {
    render(
      <ServiceToolsetCell
        service={service({
          mcp_server_ids: ["m-atlassian"],
          allowed_integration_connector_ids: ["c-jira"],
          tool_source_overlaps: [OVERLAP],
        })}
      />,
    );
    expect(screen.getByText("MCP covered")).toBeTruthy();
    const warning = screen.getByText("Overlapping tool sources");
    const title = warning.closest("[title]")?.getAttribute("title") ?? "";
    expect(title).toContain("Jira DOT (jira connector) and MCP server atlassian");
    expect(title).toContain("only the native connector links tickets");
  });
});

describe("ToolSourceOverlapNote", () => {
  it("renders while both sources are still selected", () => {
    render(
      <ToolSourceOverlapNote
        overlaps={[OVERLAP]}
        selectedMcpServerIds={["m-atlassian"]}
        selectedConnectorIds={["c-jira"]}
      />,
    );
    const note = screen.getByRole("note");
    expect(note.textContent).toContain("Jira DOT (jira connector) and MCP server atlassian");
    expect(note.textContent).toContain("deny the connector's tools in its skill");
  });

  it("disappears as soon as either source is deselected", () => {
    const { rerender, container } = render(
      <ToolSourceOverlapNote
        overlaps={[OVERLAP]}
        selectedMcpServerIds={[]}
        selectedConnectorIds={["c-jira"]}
      />,
    );
    expect(container.firstChild).toBeNull();
    rerender(
      <ToolSourceOverlapNote
        overlaps={[OVERLAP]}
        selectedMcpServerIds={["m-atlassian"]}
        selectedConnectorIds={[]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing for a new service or one without overlaps", () => {
    const { container, rerender } = render(
      <ToolSourceOverlapNote
        overlaps={undefined}
        selectedMcpServerIds={["m-atlassian"]}
        selectedConnectorIds={["c-jira"]}
      />,
    );
    expect(container.firstChild).toBeNull();
    rerender(
      <ToolSourceOverlapNote
        overlaps={[]}
        selectedMcpServerIds={["m-atlassian"]}
        selectedConnectorIds={["c-jira"]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});

describe("toolSourceOverlapSummary", () => {
  it("joins every pair on one line", () => {
    const second = { ...OVERLAP, connector_id: "c2", connector_name: "Jira DATA" };
    expect(toolSourceOverlapSummary([OVERLAP, second])).toBe(
      "Jira DOT (jira connector) and MCP server atlassian; " +
        "Jira DATA (jira connector) and MCP server atlassian",
    );
  });
});
