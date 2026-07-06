import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const streamState = vi.hoisted(() => ({
  handlers: [] as Array<{ onNotification: (n: unknown) => void; onClose?: () => void }>,
}));

const apiMocks = vi.hoisted(() => ({
  connectNotificationStream: vi.fn(
    (handlers: { onNotification: (n: unknown) => void; onClose?: () => void }) => {
      streamState.handlers.push(handlers);
      return { close: vi.fn() };
    },
  ),
  listApprovals: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import {
  createLiveEventDispatcher,
  LiveEventsProvider,
  useLiveEvents,
  usePendingApprovalsCount,
} from "@/context/liveEvents";

function notification(category: string, id = "n1") {
  return {
    id,
    event_type: `${category}.changed`,
    category,
    title: "Changed",
    body: null,
    link: null,
    incident_id: null,
    session_id: null,
    read_at: null,
    created_at: "2026-07-06T12:00:00Z",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  streamState.handlers = [];
  apiMocks.listApprovals.mockResolvedValue({ items: [], total: 0 });
});

describe("createLiveEventDispatcher", () => {
  it("filters by category and debounces event bursts", () => {
    vi.useFakeTimers();
    try {
      const dispatcher = createLiveEventDispatcher({ debounceMs: 500 });
      const handler = vi.fn();
      dispatcher.subscribe(["approval"], handler);

      dispatcher.publish(notification("incident"));
      vi.advanceTimersByTime(500);
      expect(handler).not.toHaveBeenCalled();

      dispatcher.publish(notification("approval", "n1"));
      dispatcher.publish(notification("approval", "n2"));
      vi.advanceTimersByTime(499);
      expect(handler).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);

      expect(handler).toHaveBeenCalledTimes(1);
      expect(handler.mock.calls[0][0].id).toBe("n2");
      dispatcher.clear();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("LiveEventsProvider", () => {
  function Subscriber({ category }: { category: string }) {
    useLiveEvents([category], () => {});
    return <span>{category}</span>;
  }

  function PendingCount() {
    const count = usePendingApprovalsCount(true);
    return <span>Pending {count}</span>;
  }

  it("opens one notification socket for multiple subscribers", async () => {
    render(
      <LiveEventsProvider>
        <Subscriber category="incident" />
        <Subscriber category="approval" />
      </LiveEventsProvider>,
    );

    await waitFor(() =>
      expect(apiMocks.connectNotificationStream).toHaveBeenCalledTimes(1),
    );
  });

  it("refreshes the pending approvals count on approval events", async () => {
    apiMocks.listApprovals
      .mockResolvedValueOnce({ items: [], total: 2 })
      .mockResolvedValueOnce({ items: [], total: 7 });

    render(
      <LiveEventsProvider>
        <PendingCount />
      </LiveEventsProvider>,
    );

    expect(await screen.findByText("Pending 2")).toBeTruthy();
    act(() => {
      streamState.handlers[0].onNotification(notification("approval"));
    });

    await waitFor(() => expect(screen.getByText("Pending 7")).toBeTruthy());
  });
});
