import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Avoid touching the real API client.
vi.mock("@/lib/api_reliability", () => ({
  createSLO: vi.fn(),
  updateSLO: vi.fn(),
}));

import { SLOModal } from "@/components/reliability/SLOModal";

function renderModal() {
  return render(
    <SLOModal open onClose={() => {}} onSaved={() => {}} targetId="t1" initialData={null} />,
  );
}

describe("SLOModal (v1 simplified)", () => {
  it("hides burn-rate / error-budget fields", () => {
    renderModal();
    expect(screen.queryByText(/burn/i)).toBeNull();
    expect(screen.queryByText(/error budget/i)).toBeNull();
    expect(screen.queryByText(/threshold/i)).toBeNull();
  });

  it("supports 3 decimal places on the target objective", () => {
    renderModal();
    const objective = screen.getByLabelText(/Target \(%\)/i) as HTMLInputElement;
    expect(objective.getAttribute("step")).toBe("0.001");
  });

  it("states that SLO breaches do not create incidents", () => {
    renderModal();
    expect(
      screen.getByText(/does not create incidents from SLO breaches/i),
    ).toBeTruthy();
  });
});
