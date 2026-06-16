/**
 * Default AI Autonomy Tier control — 3-tier labels (no Tier 3), default Tier 2,
 * and the Tier 0 red warning.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ updateConfig: vi.fn().mockResolvedValue({}) }));

import { TierSection } from "@/components/config/ConfigSections";
import type { ConfigResponse } from "@/lib/types";

const CONFIG = {
  tier: 2,
  logging_level: "INFO",
  audit_output: "stdout",
} as unknown as ConfigResponse;

describe("Default AI Autonomy Tier control", () => {
  it("offers Tier 0/1/2 with new labels and no Tier 3", () => {
    render(<TierSection config={CONFIG} onSaved={async () => {}} canEdit />);
    expect(screen.getByText("Default AI Autonomy Tier")).toBeTruthy();
    expect(screen.getByText("Tier 0 — Autonomous")).toBeTruthy();
    expect(screen.getByText("Tier 1 — Approval Required")).toBeTruthy();
    expect(screen.getByText("Tier 2 — Advisory Only")).toBeTruthy();
    expect(screen.queryByText(/Tier 3/)).toBeNull();
    expect(screen.queryByText(/Advise-only/i)).toBeNull();
  });

  it("defaults to Tier 2 and shows a red warning when Tier 0 is selected", () => {
    render(<TierSection config={CONFIG} onSaved={async () => {}} canEdit />);
    const select = screen.getByLabelText("Default AI Autonomy Tier") as HTMLSelectElement;
    expect(select.value).toBe("2");
    // No warning at Tier 2.
    expect(screen.queryByText(/autonomous remediation/i)).toBeNull();
    fireEvent.change(select, { target: { value: "0" } });
    expect(screen.getByText(/autonomous remediation, including destructive/i)).toBeTruthy();
  });
});
