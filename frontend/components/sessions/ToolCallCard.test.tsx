/**
 * ToolCallCard safety-class chip — renders the SKILL.md classification
 * (safe / caution / destructive / unknown) carried on the tool_call WS payload.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolCallCard } from "@/components/sessions/ToolCallCard";

function renderCard(raw: Record<string, unknown>) {
  return render(
    <ToolCallCard raw={raw} ts={new Date("2026-06-21T12:00:00Z")} />,
  );
}

describe("ToolCallCard safety-class chip", () => {
  it("renders a destructive chip with the classification dataset", () => {
    renderCard({
      tool_name: "delete_pod",
      phase: "start",
      classification: "destructive",
    });
    const chip = screen.getByTestId("safety-class-chip");
    expect(chip.getAttribute("data-classification")).toBe("destructive");
    expect(chip.textContent).toContain("Destructive");
  });

  it("renders safe and caution labels", () => {
    const { rerender } = renderCard({
      tool_name: "get_pods",
      phase: "end",
      classification: "safe",
    });
    expect(screen.getByTestId("safety-class-chip").textContent).toContain("Safe");

    rerender(
      <ToolCallCard
        raw={{ tool_name: "cordon_node", phase: "end", classification: "caution" }}
        ts={new Date()}
      />,
    );
    expect(screen.getByTestId("safety-class-chip").textContent).toContain(
      "Caution",
    );
  });

  it("renders an unclassified chip for unknown tools", () => {
    renderCard({ tool_name: "mystery", phase: "start", classification: "unknown" });
    expect(screen.getByTestId("safety-class-chip").textContent).toContain(
      "Unclassified",
    );
  });

  it("renders no chip when classification is absent (older payloads)", () => {
    renderCard({ tool_name: "get_pods", phase: "start" });
    expect(screen.queryByTestId("safety-class-chip")).toBeNull();
  });

  it("renders no chip for an unrecognised classification value", () => {
    renderCard({ tool_name: "x", phase: "start", classification: "bogus" });
    expect(screen.queryByTestId("safety-class-chip")).toBeNull();
  });
});
