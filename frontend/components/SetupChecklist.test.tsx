import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  connectNotificationStream: vi.fn(),
  getSetupChecklist: vi.fn(),
  listApprovals: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import { SetupChecklist } from "@/components/SetupChecklist";
import type { SetupChecklistResponse } from "@/lib/types";

const storage = new Map<string, string>();

function checklist(
  overrides: Partial<SetupChecklistResponse> = {},
): SetupChecklistResponse {
  return {
    model_configured: false,
    mcp_server_added: false,
    integration_connected: false,
    skill_defined: false,
    ingest_token_created: false,
    paging_service_added: false,
    all_complete: false,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  storage.clear();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        storage.set(key, value);
      },
      removeItem: (key: string) => {
        storage.delete(key);
      },
    },
  });
  apiMocks.getSetupChecklist.mockResolvedValue(checklist());
});

describe("SetupChecklist", () => {
  it("renders progress and pending setup links", async () => {
    apiMocks.getSetupChecklist.mockResolvedValue(
      checklist({
        model_configured: true,
        mcp_server_added: true,
      }),
    );

    render(<SetupChecklist />);

    expect(
      await screen.findByRole("heading", { name: /Set up OpsMender/ }),
    ).toBeTruthy();
    expect(screen.getByText("2 of 5 steps")).toBeTruthy();
    expect(screen.getByText("2 of 5 steps complete")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("2");
    expect(screen.getByRole("link", { name: /Configure an AI model/ }).getAttribute("href")).toBe(
      "/dashboard/models",
    );
    expect(screen.getByRole("link", { name: /Define a skill/ }).getAttribute("href")).toBe(
      "/dashboard/skills",
    );
    expect(screen.getByRole("link", { name: /Set up alert intake/ }).getAttribute("href")).toBe(
      "/dashboard/paging/services",
    );
  });

  it("renders nothing once setup is complete", async () => {
    apiMocks.getSetupChecklist.mockResolvedValue(
      checklist({
        model_configured: true,
        mcp_server_added: true,
        skill_defined: true,
        ingest_token_created: true,
        paging_service_added: true,
        all_complete: true,
      }),
    );

    render(<SetupChecklist />);

    await waitFor(() => expect(apiMocks.getSetupChecklist).toHaveBeenCalled());
    expect(screen.queryByRole("heading", { name: /Set up OpsMender/ })).toBeNull();
  });

  it("treats an active integration as infrastructure coverage", async () => {
    apiMocks.getSetupChecklist.mockResolvedValue(
      checklist({ integration_connected: true }),
    );

    render(<SetupChecklist />);

    expect(await screen.findByText("1 of 5 steps complete")).toBeTruthy();
    expect(screen.getByText("Connect your infrastructure").className).toContain(
      "line-through",
    );
  });

  it("refetches when the tab regains focus", async () => {
    apiMocks.getSetupChecklist
      .mockResolvedValueOnce(checklist())
      .mockResolvedValueOnce(
        checklist({
          model_configured: true,
          mcp_server_added: true,
        }),
      );

    render(<SetupChecklist />);

    expect(await screen.findByText("0 of 5 steps complete")).toBeTruthy();

    window.dispatchEvent(new Event("focus"));

    expect(await screen.findByText("2 of 5 steps complete")).toBeTruthy();
  });

  it("persists dismissal", async () => {
    render(<SetupChecklist />);

    expect(
      await screen.findByRole("heading", { name: /Set up OpsMender/ }),
    ).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Dismiss setup checklist"));

    expect(window.localStorage.getItem("opsmender:setup-checklist-dismissed")).toBe("1");
    expect(screen.queryByRole("heading", { name: /Set up OpsMender/ })).toBeNull();
  });

  it("stays silent when setup state is unavailable", async () => {
    apiMocks.getSetupChecklist.mockRejectedValue(new Error("forbidden"));

    render(<SetupChecklist />);

    await waitFor(() => expect(apiMocks.getSetupChecklist).toHaveBeenCalled());
    expect(screen.queryByRole("heading", { name: /Set up OpsMender/ })).toBeNull();
  });
});
