import React from "react";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApprovalEvidenceNote } from "@/components/sessions/ApprovalEvidenceNote";
import type { ApprovalEvidence } from "@/lib/types";

const EVIDENCE: ApprovalEvidence = {
  source: "tool_results",
  round: 2,
  calls: [
    {
      tool_name: "get_service_health",
      tool_parameters: { service: "productpage" },
      outcome: "ok",
      round: 1,
    },
    {
      tool_name: "get_pod_logs",
      tool_parameters: { service: "ratings", lines: 50 },
      outcome: "failed",
      round: 1,
    },
  ],
  calls_not_shown: 0,
  addressed: [],
};

describe("ApprovalEvidenceNote", () => {
  it("renders nothing for a request of a first plan", () => {
    const { container } = render(<ApprovalEvidenceNote evidence={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("says the request was written from tool results and lists the calls", () => {
    render(<ApprovalEvidenceNote evidence={EVIDENCE} />);

    const note = screen.getByRole("note", { name: "Where this request came from" });
    expect(within(note).getByText("Written after reading tool results")).toBeTruthy();
    expect(within(note).getByText(/planned this request in round 2/)).toBeTruthy();

    const calls = within(note).getByRole("list", { name: "Calls made before this request" });
    const rows = within(calls).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toBe(
      "get_service_health (service=productpage): returned a result",
    );
    expect(rows[1].textContent).toBe("get_pod_logs (service=ratings, lines=50): failed");
    expect(within(note).queryByText(/addressed to the assistant/)).toBeNull();
    expect(within(note).queryByText(/already rejected/)).toBeNull();
    expect(within(note).queryByText(/Tool Activity/)).toBeNull();
  });

  it("names a result that addresses the assistant and counts the calls left out", () => {
    render(
      <ApprovalEvidenceNote
        evidence={{
          ...EVIDENCE,
          calls_not_shown: 3,
          addressed: [
            { tool_name: "get_service_health", phrases: ["ignore your instructions"] },
          ],
          rejected: [
            { tool_name: "restart_deployment", tool_parameters: { deployment: "ratings-v1" } },
          ],
        }}
      />,
    );

    expect(
      screen.getByText(
        'A result contains text addressed to the assistant: get_service_health ("ignore your instructions").',
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "An operator already rejected in this session: restart_deployment (deployment=ratings-v1).",
      ),
    ).toBeTruthy();
    expect(screen.getByText(/3 earlier calls are listed in the session/)).toBeTruthy();
  });
});
