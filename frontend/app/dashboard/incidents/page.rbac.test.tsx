/**
 * Part 1/8 — New Incident + Fire Test Incident are admin-only on the Incidents
 * page; Operators and Viewers don't see them.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

const search = { current: "" };
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search.current),
  usePathname: () => "/dashboard/incidents",
}));

const toastSpies = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => toastSpies,
}));

const role = { current: "admin" };
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: "u", username: "u", role: role.current } }),
}));

const apiMocks = vi.hoisted(() => ({
  bulkIncidentAction: vi.fn(),
  combineIncidents: vi.fn(),
  createIncident: vi.fn(),
  deleteIncident: vi.fn(),
  fireTestIncident: vi.fn().mockResolvedValue({
    incident: {
      id: "inc-test",
      title: "TEST · synthetic alert",
      description: "Synthetic alert",
      status: "open",
      severity: "high",
      service_id: null,
      external_id: "test-1",
      external_source: "opsmender-test",
      created_at: "2026-06-15T00:00:00Z",
      updated_at: "2026-06-15T00:00:00Z",
    },
    resolved_tier: 2,
    auto_start_status: "skipped",
    auto_start_reason: "auto_start_skipped_non_t0",
    message:
      "Test incident created. AI session auto-start was skipped because the resolved autonomy tier is T2; only T0 may auto-start.",
  }),
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
  search.current = "";
  vi.clearAllMocks();
  apiMocks.listIncidents.mockResolvedValue({ items: [], total: 0 });
  apiMocks.bulkIncidentAction.mockResolvedValue({
    action: "resolve",
    succeeded: 2,
    failed: 0,
    items: [],
  });
});

async function renderAndSettle() {
  render(<IncidentsPage />);
  await waitFor(() => expect(apiMocks.listIncidents).toHaveBeenCalled());
}

function incident(
  id: string,
  status: "open" | "in_progress" | "resolved",
  serviceId: string,
) {
  return {
    id,
    title: `Incident ${id}`,
    description: "test incident",
    status,
    severity: "high",
    service_id: serviceId,
    service_name: serviceId,
    team_id: `team-${serviceId}`,
    team_name: `Team ${serviceId}`,
    external_id: null,
    external_source: null,
    created_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-06-15T00:00:00Z",
  };
}

describe("Incidents page RBAC", () => {
  it("shows when an incident is waiting for AI capacity", async () => {
    apiMocks.listIncidents.mockResolvedValue({
      items: [
        {
          ...incident("queued", "open", "svc-queue"),
          ai_session_active: true,
          ai_session_status: "queued",
        },
      ],
      total: 1,
    });
    await renderAndSettle();
    expect((await screen.findAllByText("AI · waiting")).length).toBeGreaterThan(0);
  });

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

  it("demotes Fire Test Incident once real incidents exist", async () => {
    role.current = "admin";
    apiMocks.listIncidents.mockResolvedValue({
      items: [incident("inc-real", "open", "svc-real")],
      total: 1,
    });
    await renderAndSettle();

    expect(screen.queryByRole("button", { name: /fire test incident/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /more incident actions/i }));
    expect(screen.getByRole("button", { name: /fire test incident/i })).toBeTruthy();
  });

  it("requires an active service before creating a manual incident", async () => {
    apiMocks.listServices.mockResolvedValue({
      items: [
        {
          id: "svc-active",
          team_id: "team-1",
          name: "Checkout API",
          slug: "checkout-api",
          description: null,
          priority: "P1",
          preferred_mcp_server_ids: [],
          model_config_ids: [],
          ai_default_tier: null,
          intake_url: null,
          external_refs: null,
          is_active: true,
          created_at: "2026-06-15T00:00:00Z",
        },
        {
          id: "svc-disabled",
          team_id: "team-1",
          name: "Disabled Service",
          slug: "disabled-service",
          description: null,
          priority: "P2",
          preferred_mcp_server_ids: [],
          model_config_ids: [],
          ai_default_tier: null,
          intake_url: null,
          external_refs: null,
          is_active: false,
          created_at: "2026-06-15T00:00:00Z",
        },
      ],
      total: 2,
    });
    await renderAndSettle();
    fireEvent.click(screen.getAllByRole("button", { name: /new incident/i })[0]);

    const serviceSelect = await screen.findByLabelText("Service");
    expect(screen.getByText("Checkout API")).toBeTruthy();
    expect(screen.queryByText("Disabled Service")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Create" }).hasAttribute("disabled"),
    ).toBe(true);

    fireEvent.change(serviceSelect, { target: { value: "svc-active" } });
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Database unavailable" },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Primary is not responding" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(apiMocks.createIncident).toHaveBeenCalledWith(
        expect.objectContaining({ service_id: "svc-active" }),
      ),
    );
  });

  it("shows row delete only for admins", async () => {
    apiMocks.listIncidents.mockResolvedValue({
      items: [
        {
          id: "inc-delete",
          title: "Old incident",
          description: "cleanup",
          status: "resolved",
          severity: "low",
          service_id: null,
          external_id: null,
          external_source: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      total: 1,
    });
    role.current = "admin";
    await renderAndSettle();
    expect(
      screen.getAllByRole("button", { name: "Delete incident Old incident" }).length,
    ).toBeGreaterThan(0);
  });

  it("hides row delete from operators", async () => {
    apiMocks.listIncidents.mockResolvedValue({
      items: [
        {
          id: "inc-delete",
          title: "Old incident",
          description: "cleanup",
          status: "resolved",
          severity: "low",
          service_id: null,
          external_id: null,
          external_source: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      total: 1,
    });
    role.current = "operator";
    await renderAndSettle();
    expect(
      screen.queryByRole("button", { name: "Delete incident Old incident" }),
    ).toBeNull();
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

  it("opens the fire-test modal via ?test=1 for admin", async () => {
    role.current = "admin";
    search.current = "test=1";
    await renderAndSettle();
    // Button + modal title both read "Fire Test Incident" → at least 2 nodes.
    await waitFor(() =>
      expect(
        screen.queryAllByText(/fire test incident/i).length,
      ).toBeGreaterThan(1),
    );
    expect(
      screen.getByText(/An AI session will only auto-start if the resolved autonomy tier is T0/i),
    ).toBeTruthy();
    expect(screen.queryByText(/immediately starts a session/i)).toBeNull();
  });

  it("submits fire tests through the policy-aware endpoint", async () => {
    role.current = "admin";
    search.current = "test=1";
    await renderAndSettle();
    const buttons = await screen.findAllByRole("button", {
      name: /fire test incident/i,
    });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() =>
      expect(apiMocks.fireTestIncident).toHaveBeenCalledWith({
        service_id: undefined,
      }),
    );
  });

  it("does NOT open the fire-test modal via ?test=1 for operator", async () => {
    role.current = "operator";
    search.current = "test=1";
    await renderAndSettle();
    // No button (hidden) and the deep link must not open the modal either.
    expect(screen.queryByText(/fire test incident/i)).toBeNull();
  });

  it("does NOT open the create modal via ?new=1 for viewer", async () => {
    role.current = "viewer";
    search.current = "new=1";
    await renderAndSettle();
    expect(screen.queryByText(/create incident/i)).toBeNull();
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

  it("confirms cross-service resolve for admins", async () => {
    role.current = "admin";
    apiMocks.listIncidents.mockResolvedValue({
      items: [
        incident("inc-1", "open", "svc-1"),
        incident("inc-2", "in_progress", "svc-2"),
      ],
      total: 2,
    });
    await renderAndSettle();
    fireEvent.click(
      screen.getAllByRole("checkbox", {
        name: "Select all rows on this page",
      })[0],
    );
    fireEvent.click(screen.getByTestId("incident-actions-trigger"));
    const resolve = screen.getByTestId("incident-action-resolve");
    expect(resolve.hasAttribute("disabled")).toBe(false);
    fireEvent.click(resolve);
    expect(
      screen.getByText(/Are you sure you want to mark 2 incidents as resolved/),
    ).toBeTruthy();
    fireEvent.click(screen.getByTestId("confirm-incident-bulk-action"));
    await waitFor(() =>
      expect(apiMocks.bulkIncidentAction).toHaveBeenCalledWith(
        "resolve",
        ["inc-1", "inc-2"],
        undefined,
      ),
    );
  });

  it("allows operators to resolve one-service selections", async () => {
    role.current = "operator";
    apiMocks.listIncidents.mockResolvedValue({
      items: [
        incident("inc-1", "open", "svc-1"),
        incident("inc-2", "in_progress", "svc-1"),
      ],
      total: 2,
    });
    await renderAndSettle();
    fireEvent.click(
      screen.getAllByRole("checkbox", {
        name: "Select all rows on this page",
      })[0],
    );
    fireEvent.click(screen.getByTestId("incident-actions-trigger"));
    expect(
      screen.getByTestId("incident-action-resolve").hasAttribute("disabled"),
    ).toBe(false);
  });

  it("blocks operator lifecycle actions across services", async () => {
    role.current = "operator";
    apiMocks.listIncidents.mockResolvedValue({
      items: [
        incident("inc-1", "open", "svc-1"),
        incident("inc-2", "in_progress", "svc-2"),
      ],
      total: 2,
    });
    await renderAndSettle();
    fireEvent.click(
      screen.getAllByRole("checkbox", {
        name: "Select all rows on this page",
      })[0],
    );
    fireEvent.click(screen.getByTestId("incident-actions-trigger"));
    expect(
      screen.getByTestId("incident-action-resolve").hasAttribute("disabled"),
    ).toBe(true);
    expect(screen.queryByTestId("incident-action-delete")).toBeNull();
  });

  it("greys Reopen for mixed states and enables it for resolved incidents", async () => {
    role.current = "admin";
    apiMocks.listIncidents.mockResolvedValue({
      items: [
        incident("inc-1", "resolved", "svc-1"),
        incident("inc-2", "open", "svc-1"),
      ],
      total: 2,
    });
    await renderAndSettle();
    fireEvent.click(
      screen.getAllByRole("checkbox", {
        name: "Select all rows on this page",
      })[0],
    );
    fireEvent.click(screen.getByTestId("incident-actions-trigger"));
    expect(
      screen.getByTestId("incident-action-reopen").hasAttribute("disabled"),
    ).toBe(true);
  });

  it("offers dynamic confirmed bulk deletion only to admins", async () => {
    role.current = "admin";
    apiMocks.listIncidents.mockResolvedValue({
      items: [
        incident("inc-1", "resolved", "svc-1"),
        incident("inc-2", "resolved", "svc-2"),
      ],
      total: 2,
    });
    await renderAndSettle();
    fireEvent.click(
      screen.getAllByRole("checkbox", {
        name: "Select all rows on this page",
      })[0],
    );
    fireEvent.click(screen.getByTestId("incident-actions-trigger"));
    expect(screen.getByTestId("incident-action-delete").textContent).toContain(
      "Delete all",
    );
    fireEvent.click(screen.getByTestId("incident-action-delete"));
    expect(
      screen.getByText(/permanently delete 2 incidents/i),
    ).toBeTruthy();
    fireEvent.click(screen.getByTestId("confirm-incident-bulk-action"));
    await waitFor(() =>
      expect(apiMocks.bulkIncidentAction).toHaveBeenCalledWith(
        "delete",
        ["inc-1", "inc-2"],
        undefined,
      ),
    );
  });
});
