import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { IntakeUrlHint, IntakeUrlOnceDialog } from "@/components/paging/IntakeUrl";

describe("IntakeUrlHint", () => {
  it("shows the masked hint and nothing to copy", () => {
    render(
      <IntakeUrlHint
        hint="/api/v1/intake/svc_F8Gn…"
        publicBaseUrl="https://ops.example.com"
      />,
    );
    expect(screen.getByText("https://ops.example.com/api/v1/intake/svc_F8Gn…")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /copy/i })).toBeNull();
  });

  it("says when a service has no URL yet", () => {
    render(<IntakeUrlHint hint={null} publicBaseUrl={null} />);
    expect(screen.getByText("No URL yet")).toBeTruthy();
  });
});

describe("IntakeUrlOnceDialog", () => {
  it("shows the full URL once, with a copy button", () => {
    const onClose = vi.fn();
    render(
      <IntakeUrlOnceDialog
        shown={{ name: "checkout", url: "/api/v1/intake/svc_full-secret-token" }}
        publicBaseUrl="https://ops.example.com/"
        onClose={onClose}
      />,
    );
    expect(screen.getByText("Intake URL for checkout")).toBeTruthy();
    expect(
      screen.getByText("https://ops.example.com/api/v1/intake/svc_full-secret-token"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy intake url" })).toBeTruthy();
    expect(screen.getByText(/won't be shown again/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("renders nothing when there is no URL to show", () => {
    render(<IntakeUrlOnceDialog shown={null} publicBaseUrl={null} onClose={() => {}} />);
    expect(screen.queryByText(/won't be shown again/)).toBeNull();
  });
});
