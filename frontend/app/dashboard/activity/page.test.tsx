import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuditEntryResponse } from "@/lib/types";

const toastSpies = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => toastSpies,
}));

const apiMocks = vi.hoisted(() => ({
  downloadAuditCsv: vi.fn(),
  listAudit: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import ActivityPage, {
  groupAuditEntriesBySession,
} from "@/app/dashboard/activity/page";

function auditEntry(
  overrides: Partial<AuditEntryResponse> & { id: string; timestamp: string },
): AuditEntryResponse {
  const { id, timestamp, ...rest } = overrides;
  return {
    id,
    session_id: "aaaaaaaa-1111-2222-3333-444444444444",
    timestamp,
    tier: 1,
    entry_type: "pre",
    tool_name: "kubectl.get_pods",
    tool_parameters: null,
    result: null,
    permitted: true,
    block_reason: null,
    duration_ms: 125,
    ...rest,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.downloadAuditCsv.mockResolvedValue(undefined);
  apiMocks.listAudit.mockResolvedValue({ items: [], total: 0 });
});

describe("Activity session grouping", () => {
  it("groups entries by newest session, preserves the system bucket, and counts blocked rows", () => {
    const groups = groupAuditEntriesBySession([
      auditEntry({
        id: "a-old",
        timestamp: "2026-07-06T12:00:00Z",
        session_id: "aaaaaaaa-1111-2222-3333-444444444444",
      }),
      auditEntry({
        id: "system",
        timestamp: "2026-07-06T12:01:00Z",
        session_id: null as unknown as string,
        tool_name: null,
      }),
      auditEntry({
        id: "a-blocked",
        timestamp: "2026-07-06T12:02:00Z",
        session_id: "aaaaaaaa-1111-2222-3333-444444444444",
        permitted: false,
      }),
      auditEntry({
        id: "b-newest",
        timestamp: "2026-07-06T12:05:00Z",
        session_id: "bbbbbbbb-1111-2222-3333-444444444444",
      }),
    ]);

    expect(groups.map((group) => group.sessionId)).toEqual([
      "bbbbbbbb-1111-2222-3333-444444444444",
      "aaaaaaaa-1111-2222-3333-444444444444",
      null,
    ]);
    expect(groups[1].blockedCount).toBe(1);
    expect(groups[1].entries.map((entry) => entry.id)).toEqual([
      "a-blocked",
      "a-old",
    ]);
    expect(groups[2].key).toBe("system");
  });

  it("defaults to grouped sections, filters grouped rows, and toggles back to the flat table", async () => {
    apiMocks.listAudit.mockResolvedValue({
      items: [
        auditEntry({
          id: "b-newest",
          timestamp: "2026-07-06T12:05:00Z",
          session_id: "bbbbbbbb-1111-2222-3333-444444444444",
          tool_name: "kubectl.get_pods",
        }),
        auditEntry({
          id: "a-rollout",
          timestamp: "2026-07-06T12:02:00Z",
          session_id: "aaaaaaaa-1111-2222-3333-444444444444",
          tool_name: "kubectl.rollout_restart",
          permitted: false,
        }),
      ],
      total: 2,
    });

    render(<ActivityPage />);

    const groupToggle = await screen.findByRole("switch", {
      name: /group by session/i,
    });
    expect(groupToggle.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/bbbbbbbb/)).toBeTruthy();
    expect(await screen.findByText("kubectl.get_pods")).toBeTruthy();
    expect(screen.queryByText("kubectl.rollout_restart")).toBeNull();

    const disabledDateRange = screen.getByTitle(
      "Date range is available in flat table mode.",
    ) as HTMLButtonElement;
    expect(disabledDateRange.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/search grouped activity/i), {
      target: { value: "rollout" },
    });

    await waitFor(() => {
      expect(screen.getByText(/aaaaaaaa/)).toBeTruthy();
      expect(screen.getByText("kubectl.rollout_restart")).toBeTruthy();
    });
    expect(screen.queryByText(/bbbbbbbb/)).toBeNull();

    fireEvent.click(groupToggle);

    await waitFor(() => {
      expect(groupToggle.getAttribute("aria-checked")).toBe("false");
      expect(screen.getByLabelText(/timestamp from/i)).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: /columns/i })).toBeTruthy();
    expect(screen.getAllByText("kubectl.get_pods").length).toBeGreaterThan(0);
  });
});
