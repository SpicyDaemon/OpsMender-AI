/**
 * F3: the Tier 1 one-liner names every action class that pauses for an
 * operator's approval, as the matrix below it and the tier policy do.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TierCapabilitySummary } from "./TierCapabilitySummary";

describe("TierCapabilitySummary", () => {
  it("says caution actions pause for approval at Tier 1", () => {
    render(<TierCapabilitySummary tier={1} defaultCollapsed />);

    expect(
      screen.getByText(
        "Tier 1 · Approval Required: AI investigates and proposes. Caution, destructive, and generic-command actions pause for operator approval.",
      ),
    ).toBeTruthy();
  });
});
