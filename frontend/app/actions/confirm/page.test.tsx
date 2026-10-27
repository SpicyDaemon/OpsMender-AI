import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// One router object, as Next.js gives every render.
const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
const routerReplace = router.replace;

vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

vi.mock("@/components/auth/AuthShell", () => ({
  AuthShell: ({
    title,
    description,
    footer,
    children,
  }: {
    title: string;
    description: string;
    footer: React.ReactNode;
    children: React.ReactNode;
  }) => (
    <div>
      <h1>{title}</h1>
      <p>{description}</p>
      {children}
      <footer>{footer}</footer>
    </div>
  ),
}));

const apiMocks = vi.hoisted(() => ({
  getToken: vi.fn(),
  previewActionLink: vi.fn(),
  confirmActionLink: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import ActionLinkConfirmPage from "@/app/actions/confirm/page";

const READY = {
  state: "ready",
  message: null,
  action: "acknowledge",
  action_label: "Acknowledge",
  incident_id: "inc-1",
  incident_title: "Checkout errors",
  incident_status: "open",
  incident_severity: "high",
  channel_name: "Ops channel",
  expires_at: "2026-10-28T12:00:00Z",
  can_act: true,
  used_at: null,
  used_result: null,
};

function openAt(hash: string) {
  window.history.replaceState(null, "", `/actions/confirm${hash}`);
}

describe("action link confirm page (M1-99)", () => {
  beforeEach(() => {
    sessionStorage.clear();
    apiMocks.getToken.mockReturnValue("session-jwt");
    apiMocks.previewActionLink.mockReset();
    apiMocks.confirmActionLink.mockReset();
    routerReplace.mockReset();
  });

  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("shows the action and acts only when Confirm is pressed", async () => {
    openAt("#token=abc.def");
    apiMocks.previewActionLink.mockResolvedValue(READY);
    apiMocks.confirmActionLink.mockResolvedValue({
      action: "acknowledge",
      status: "acknowledged",
      message: "You acknowledged the incident.",
      incident_id: "inc-1",
      session_id: null,
      changed: true,
    });
    render(<ActionLinkConfirmPage />);

    expect(await screen.findByText("Acknowledge this incident?")).toBeTruthy();
    expect(apiMocks.previewActionLink).toHaveBeenCalledWith("abc.def");
    expect(screen.getByText("Checkout errors")).toBeTruthy();
    expect(screen.getByText("Ops channel")).toBeTruthy();
    // Opening the link never acts.
    expect(apiMocks.confirmActionLink).not.toHaveBeenCalled();
    // Signed in: nothing to come back to.
    expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Confirm: Acknowledge" }));
    expect(await screen.findByText("Done")).toBeTruthy();
    expect(apiMocks.confirmActionLink).toHaveBeenCalledTimes(1);
    expect(apiMocks.confirmActionLink).toHaveBeenCalledWith("abc.def");
    expect(screen.getByText("You acknowledged the incident.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open the incident" }).getAttribute("href")).toBe(
      "/dashboard/incidents/detail?id=inc-1",
    );
  });

  it("sends a person without a session to sign in and remembers the link", async () => {
    openAt("#token=abc.def");
    apiMocks.getToken.mockReturnValue(null);
    render(<ActionLinkConfirmPage />);
    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith("/login"));
    expect(sessionStorage.getItem("opsmender_return_to")).toBe("/actions/confirm#token=abc.def");
    expect(apiMocks.previewActionLink).not.toHaveBeenCalled();
  });

  it("says Nothing changed when the action found its work done", async () => {
    openAt("#token=abc.def");
    apiMocks.previewActionLink.mockResolvedValue(READY);
    apiMocks.confirmActionLink.mockResolvedValue({
      action: "acknowledge",
      status: "already_owned",
      message: "Another responder already owns this incident.",
      incident_id: "inc-1",
      session_id: null,
      changed: false,
    });
    render(<ActionLinkConfirmPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm: Acknowledge" }));
    expect(await screen.findByText("Nothing changed")).toBeTruthy();
    expect(screen.queryByText("Done")).toBeNull();
  });

  it("starts over when the link in the address bar changes", async () => {
    openAt("#token=abc.def");
    apiMocks.previewActionLink
      .mockResolvedValueOnce(READY)
      .mockResolvedValueOnce({ ...READY, action: "resolve", action_label: "Resolve", incident_title: "Other" });
    apiMocks.confirmActionLink.mockResolvedValue({
      action: "resolve",
      status: "resolved",
      message: "You resolved the incident.",
      incident_id: "inc-2",
      session_id: null,
      changed: true,
    });
    render(<ActionLinkConfirmPage />);
    expect(await screen.findByText("Acknowledge this incident?")).toBeTruthy();
    act(() => {
      window.history.replaceState(null, "", "/actions/confirm#token=new.link");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(await screen.findByText("Resolve this incident?")).toBeTruthy();
    expect(screen.queryByText("Acknowledge this incident?")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Confirm: Resolve" }));
    await screen.findByText("Done");
    expect(apiMocks.confirmActionLink).toHaveBeenCalledWith("new.link");
  });

  it("sends a token that does not decode to the server instead of failing", async () => {
    openAt("#token=%E0%A4%A");
    apiMocks.previewActionLink.mockResolvedValue({
      ...READY,
      state: "invalid",
      message: "This link is not valid.",
      incident_id: null,
      can_act: false,
    });
    render(<ActionLinkConfirmPage />);
    expect(await screen.findByText("This link is not valid")).toBeTruthy();
    expect(apiMocks.previewActionLink).toHaveBeenCalledWith("%E0%A4%A");
  });

  it("says when a link has no token", async () => {
    openAt("");
    render(<ActionLinkConfirmPage />);
    expect(await screen.findByText("This link cannot be used")).toBeTruthy();
    expect(apiMocks.previewActionLink).not.toHaveBeenCalled();
  });

  it("disables Confirm for a role that cannot act", async () => {
    openAt("#token=abc.def");
    apiMocks.previewActionLink.mockResolvedValue({ ...READY, can_act: false });
    render(<ActionLinkConfirmPage />);
    const button = await screen.findByRole("button", { name: "Confirm: Acknowledge" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText("Your role cannot act on incidents. Ask an Operator or an Admin."),
    ).toBeTruthy();
  });

  it("explains an expired link and points at the incident", async () => {
    openAt("#token=abc.def");
    apiMocks.previewActionLink.mockResolvedValue({
      ...READY,
      state: "expired",
      message: "This link has expired. Open the incident to act there.",
      can_act: false,
    });
    render(<ActionLinkConfirmPage />);
    expect(await screen.findByText("This link has expired")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("link", { name: "Open the incident" })).toBeTruthy();
  });

  it("shows a refusal from the server and keeps the page", async () => {
    openAt("#token=abc.def");
    apiMocks.previewActionLink.mockResolvedValue(READY);
    apiMocks.confirmActionLink.mockRejectedValue(
      new Error("Only an admin, a member of this incident's team or someone its Escalation Chain paged can acknowledge it."),
    );
    render(<ActionLinkConfirmPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm: Acknowledge" }));
    expect(await screen.findByText(/a member of this incident's team/)).toBeTruthy();
    expect(screen.getByText("Acknowledge this incident?")).toBeTruthy();
    await waitFor(() => expect(apiMocks.previewActionLink).toHaveBeenCalledTimes(2));
  });

  it("switches to the used view when someone else used the link first", async () => {
    openAt("#token=abc.def");
    apiMocks.previewActionLink
      .mockResolvedValueOnce(READY)
      .mockResolvedValueOnce({
        ...READY,
        state: "used",
        message: "This link was already used.",
        can_act: false,
        used_at: "2026-10-27T12:00:00Z",
        used_result: "acknowledged",
      });
    apiMocks.confirmActionLink.mockRejectedValue(new Error("This link was already used."));
    render(<ActionLinkConfirmPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm: Acknowledge" }));
    expect(await screen.findByText("This link was already used")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
