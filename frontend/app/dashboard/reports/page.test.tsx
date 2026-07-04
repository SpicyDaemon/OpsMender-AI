import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "u-admin", username: "admin", role: "admin", primary_org_id: "org-1" },
  }),
}));

const apiMocks = vi.hoisted(() => ({
  createReportSchedule: vi.fn(),
  deleteReportSchedule: vi.fn(),
  downloadIncidentReport: vi.fn(),
  listReportSchedules: vi.fn(),
  updateReportSchedule: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import ReportsPage from "@/app/dashboard/reports/page";

beforeEach(() => {
  apiMocks.createReportSchedule.mockResolvedValue({});
  apiMocks.deleteReportSchedule.mockResolvedValue(undefined);
  apiMocks.downloadIncidentReport.mockResolvedValue(new Blob([""], { type: "text/plain" }));
  apiMocks.listReportSchedules.mockResolvedValue({ items: [], total: 0 });
  apiMocks.updateReportSchedule.mockResolvedValue({});
});

describe("Reports page", () => {
  it("shows a useful empty state when no report schedules exist", async () => {
    render(<ReportsPage />);

    expect(await screen.findByText("No scheduled reports yet")).toBeTruthy();
    expect(
      screen.getByText(/Create a weekly, monthly, or quarterly email report/i),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /create first schedule/i }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Name")));
  });
});
