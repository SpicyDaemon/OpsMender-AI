/**
 * Part 1/8 — New Incident + Fire Test Incident are admin-only on the Incidents
 * page; Operators and Viewers don't see them.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/dashboard/incidents",
}));

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const role = { current: "admin" };
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: "u", username: "u", role: role.current } }),
}));

const apiMocks = vi.hoisted(() => ({
  bulkIncidentAction: vi.fn(),
  createIncident: vi.fn(),
  createSession: vi.fn(),
  getConfig: vi.fn().mockResolvedValue({ tier: 2 }),
  listIncidents: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  listServices: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  listTeams: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  updateIncident: vi.fn(),
  getSetupChecklist: vi.fn().mockResolvedValue({ all_complete: true }),
}));
vi.mock("@/lib/api", () => apiMocks);

import IncidentsPage from "@/app/dashboard/incidents/page";

beforeEach(() => {
  const store: Record<string, string> = {};
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => {
        store[k] = v;
      },
      removeItem: (k: string) => {
        delete store[k];
      },
      clear: () => {},
    },
  });
  role.current = "admin";
});

async function renderAndSettle() {
  render(<IncidentsPage />);
  await waitFor(() => expect(apiMocks.listIncidents).toHaveBeenCalled());
}

describe("Incidents page RBAC", () => {
  it("shows New Incident + Fire Test Incident for admin", async () => {
    role.current = "admin";
    await renderAndSettle();
    await waitFor(() =>
      expect(
        screen.queryAllByRole("button", { name: /new incident/i }).length,
      ).toBeGreaterThan(0),
    );
    expect(
      screen.queryAllByRole("button", { name: /fire test incident/i }).length,
    ).toBeGreaterThan(0);
  });

  it("hides them for operator", async () => {
    role.current = "operator";
    await renderAndSettle();
    expect(screen.queryByRole("button", { name: /new incident/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /fire test incident/i })).toBeNull();
  });

  it("hides them for viewer", async () => {
    role.current = "viewer";
    await renderAndSettle();
    expect(screen.queryByRole("button", { name: /new incident/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /fire test incident/i })).toBeNull();
  });

  it("renders a Responder column with the responder state", async () => {
    role.current = "admin";
    apiMocks.listIncidents.mockResolvedValue({
      items: [
        {
          id: "inc-1",
          title: "DB outage",
          description: "down",
          status: "open",
          severity: "high",
          service_id: null,
          external_id: null,
          external_source: null,
          created_at: "2026-06-01T00:00:00Z",
          updated_at: "2026-06-01T00:00:00Z",
          responder_state: "awaiting",
          responder_user_id: "u1",
          responder_display_name: "Alice",
        },
      ],
      total: 1,
    });
    await renderAndSettle();
    await waitFor(() => expect(screen.getAllByText(/Awaiting Alice/).length).toBeGreaterThan(0));
    expect(screen.getAllByText("Responder").length).toBeGreaterThan(0);
  });
});
