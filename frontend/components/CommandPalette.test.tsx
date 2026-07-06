import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IncidentListResponse, IncidentResponse } from "@/lib/types";

const navigateMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/use-dashboard-navigation", () => ({
  useDashboardNavigation: () => navigateMock,
}));

const apiMocks = vi.hoisted(() => ({
  listIncidents: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import { CommandPalette } from "@/components/CommandPalette";

type Deferred = {
  resolve: (value: IncidentListResponse) => void;
};

function incident(overrides: Partial<IncidentResponse>): IncidentResponse {
  const now = new Date().toISOString();
  return {
    id: "incident-1",
    title: "Sample incident",
    description: "Synthetic incident",
    status: "open",
    severity: "high",
    service_id: null,
    external_id: null,
    external_source: null,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

describe("CommandPalette incident search", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) =>
      window.setTimeout(cb, 0),
    );
    navigateMock.mockClear();
    apiMocks.listIncidents.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("debounces incident search, drops stale responses, and opens the selected incident", async () => {
    const deferreds: Deferred[] = [];
    apiMocks.listIncidents.mockImplementation(
      () =>
        new Promise<IncidentListResponse>((resolve) => {
          deferreds.push({ resolve });
        }),
    );

    render(<CommandPalette />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const input = screen.getByTestId("command-palette-input");

    fireEvent.change(input, { target: { value: "s" } });
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(apiMocks.listIncidents).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "sa" } });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(apiMocks.listIncidents).toHaveBeenCalledTimes(1);
    expect(apiMocks.listIncidents).toHaveBeenLastCalledWith({ q: "sa", limit: 5 });

    fireEvent.change(input, { target: { value: "sample #2" } });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(apiMocks.listIncidents).toHaveBeenCalledTimes(2);
    expect(apiMocks.listIncidents).toHaveBeenLastCalledWith({
      q: "sample #2",
      limit: 5,
    });

    await act(async () => {
      deferreds[1].resolve({
        items: [
          incident({
            id: "incident-2",
            title: "sample #2 checkout outage",
            severity: "critical",
          }),
        ],
        total: 1,
      });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("Incidents")).toBeTruthy();
    expect(screen.getByText("sample #2 checkout outage")).toBeTruthy();
    expect(screen.getByText("critical")).toBeTruthy();
    expect(screen.getByText("just now")).toBeTruthy();

    await act(async () => {
      deferreds[0].resolve({
        items: [incident({ id: "incident-stale", title: "stale result" })],
        total: 1,
      });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.queryByText("stale result")).toBeNull();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(navigateMock).toHaveBeenCalledWith(
      "/dashboard/incidents/detail?id=incident-2",
    );
  });
});
