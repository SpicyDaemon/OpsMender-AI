/**
 * Modal — tall content must stay reachable.
 *
 * Regression guard for the bug where a modal taller than the viewport
 * overflowed off-screen with no scroll, hiding the footer actions (you had to
 * zoom the browser out to reach "Create Connector"). The panel is now capped to
 * the viewport and the body scrolls, so the footer is always reachable.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { Modal } from "@/components/ui/Modal";

describe("Modal", () => {
  it("caps the panel to the viewport and makes the body scrollable", () => {
    render(
      <Modal open onClose={() => {}} title="Add Notification Channel">
        <div style={{ height: "5000px" }}>tall content</div>
        <button type="button">Create Connector</button>
      </Modal>,
    );

    const body = screen.getByTestId("modal-body");
    // Body scrolls instead of pushing the footer off-screen.
    expect(body.className).toContain("overflow-y-auto");
    // The footer action stays in the scrollable region (reachable).
    expect(within(body).getByText("Create Connector")).toBeTruthy();

    // The panel (body's parent) is height-capped to the viewport.
    const panel = body.parentElement!;
    expect(panel.className).toMatch(/max-h-\[/);
    expect(panel.className).toContain("flex-col");
  });

  it("does not render when closed", () => {
    render(
      <Modal open={false} onClose={() => {}} title="Hidden">
        <div>nope</div>
      </Modal>,
    );
    expect(screen.queryByTestId("modal-body")).toBeNull();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Esc test">
        <div>content</div>
      </Modal>,
    );
    const event = new KeyboardEvent("keydown", { key: "Escape" });
    document.dispatchEvent(event);
    expect(onClose).toHaveBeenCalled();
  });
});
