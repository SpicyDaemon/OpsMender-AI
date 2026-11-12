import { describe, expect, it } from "vitest";

import { approvalToolName, namesItsTool } from "./approvalAction";

describe("approvalToolName", () => {
  it("reads tool_name, the tool that runs, whatever else the record holds", () => {
    // M1-173: the model writes a session's record and can add the other keys.
    expect(
      approvalToolName({
        tool_name: "delete_pod",
        tool: "get_pods",
        name: "list_pods",
        action: "read",
      }),
    ).toBe("delete_pod");
    expect(approvalToolName({ tool: "get_pods", tool_name: " delete_pod " })).toBe(
      "delete_pod",
    );
  });

  it("does not turn to another key when tool_name is there and names nothing", () => {
    expect(approvalToolName({ tool_name: "", tool: "get_pods" })).toBeNull();
    expect(approvalToolName({ tool_name: "   ", name: "get_pods" })).toBeNull();
    expect(approvalToolName({ tool_name: 7, action: "get_pods" })).toBeNull();
    expect(approvalToolName({ tool_name: false, tool: "get_pods" })).toBeNull();
    expect(approvalToolName({ tool_name: ["delete_pod"], tool: "get_pods" })).toBeNull();
    expect(approvalToolName({ tool_name: { is: "delete_pod" }, tool: "get_pods" })).toBeNull();
  });

  it("reads an older record as the Approvals page always did", () => {
    // The first of tool, name and action that is there, if it is text.
    expect(approvalToolName({ tool: "restart_pod", name: "other" })).toBe("restart_pod");
    expect(approvalToolName({ name: "legacy_tool", action: "other" })).toBe("legacy_tool");
    expect(approvalToolName({ action: "legacy_action" })).toBe("legacy_action");
    expect(approvalToolName({ tool: null, name: "legacy_tool" })).toBe("legacy_tool");
    expect(approvalToolName({ tool: "  ", name: "legacy_tool" })).toBeNull();
    expect(approvalToolName({ tool: 7, action: "legacy_action" })).toBeNull();
    expect(approvalToolName({ tool_name: null, tool: "restart_pod" })).toBe("restart_pod");
  });

  it("names nothing for a record without a tool and for what is not a record", () => {
    expect(approvalToolName({})).toBeNull();
    expect(approvalToolName({ namespace: "prod" })).toBeNull();
    expect(approvalToolName(null)).toBeNull();
    expect(approvalToolName(undefined)).toBeNull();
    expect(approvalToolName("delete_pod")).toBeNull();
    expect(approvalToolName(["tool_name", "delete_pod"])).toBeNull();
  });
});

describe("namesItsTool", () => {
  it("is true for a record with a tool_name, whatever it holds", () => {
    expect(namesItsTool({ tool_name: "delete_pod" })).toBe(true);
    expect(namesItsTool({ tool_name: "" })).toBe(true);
    expect(namesItsTool({ tool_name: 7 })).toBe(true);
  });

  it("is false for an older record and for what is not a record", () => {
    expect(namesItsTool({ tool: "restart_pod" })).toBe(false);
    expect(namesItsTool({ tool_name: null })).toBe(false);
    expect(namesItsTool(null)).toBe(false);
    expect(namesItsTool(["tool_name"])).toBe(false);
  });
});
