/**
 * Incident timeline — v1.2 Phase 4 comments + notification history.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const apiMocks = vi.hoisted(() => ({ createIncidentComment: vi.fn() }));
vi.mock("@/lib/api", () => apiMocks);

import { IncidentTimeline } from "@/components/incidents/IncidentTimeline";
import type { IncidentTimelineItemResponse } from "@/lib/types";

function baseProps() {
  return {
    items: [] as IncidentTimelineItemResponse[],
    error: "",
    activeSessionId: "",
    onSelectSession: vi.fn(),
    onStartSession: vi.fn(),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.createIncidentComment.mockResolvedValue({ id: "c1" });
});

describe("IncidentTimeline comments", () => {
  it("posts a comment and refreshes", async () => {
    const onCommentAdded = vi.fn();
    render(
      <IncidentTimeline
        {...baseProps()}
        incidentId="inc-1"
        canComment
        onCommentAdded={onCommentAdded}
      />,
    );
    fireEvent.change(screen.getByLabelText("Add a comment"), {
      target: { value: "Rolling back now." },
    });
    fireEvent.click(screen.getByRole("button", { name: /comment/i }));
    await waitFor(() =>
      expect(apiMocks.createIncidentComment).toHaveBeenCalledWith(
        "inc-1",
        "Rolling back now.",
      ),
    );
    await waitFor(() => expect(onCommentAdded).toHaveBeenCalled());
  });

  it("hides the composer for viewers (no canComment)", () => {
    render(<IncidentTimeline {...baseProps()} incidentId="inc-1" />);
    expect(screen.queryByLabelText("Add a comment")).toBeNull();
  });

  it("renders comment and notification timeline items", () => {
    const items: IncidentTimelineItemResponse[] = [
      {
        id: "comment:c1",
        happened_at: "2026-06-14T01:00:00Z",
        lane: "comment",
        event_type: "comment",
        title: "alice commented",
        body: "Looks like a disk issue.",
      } as IncidentTimelineItemResponse,
      {
        id: "notification:n1",
        happened_at: "2026-06-14T00:30:00Z",
        lane: "notification",
        event_type: "notification_sent",
        title: "Notified Slack",
        body: "acknowledged → C123",
        status: "delivered",
      } as IncidentTimelineItemResponse,
    ];
    render(<IncidentTimeline {...baseProps()} items={items} />);
    expect(screen.getByText("Looks like a disk issue.")).toBeTruthy();
    expect(screen.getByText("Notified Slack")).toBeTruthy();
  });
});
