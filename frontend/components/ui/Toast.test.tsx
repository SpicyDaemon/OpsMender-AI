import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider, useToast } from "@/components/ui/Toast";

function TriggerToast() {
  const toast = useToast();
  return (
    <button type="button" onClick={() => toast.success("Saved changes")}>
      Show toast
    </button>
  );
}

describe("ToastProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps dismissed toasts mounted during the exit fade", () => {
    render(
      <ToastProvider>
        <TriggerToast />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText("Show toast"));
    const toast = screen.getByText("Saved changes").closest("[role='status']");
    expect(toast?.className).toContain("ops-toast");
    expect(toast?.className).not.toContain("ops-toast--closing");

    fireEvent.click(screen.getByLabelText("Dismiss notification"));
    expect(toast?.className).toContain("ops-toast--closing");
    expect(screen.getByText("Saved changes")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(179);
    });
    expect(screen.getByText("Saved changes")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(screen.queryByText("Saved changes")).toBeNull();
  });
});
