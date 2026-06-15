import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { MultiSelect } from "@/components/ui/MultiSelect";

const OPTIONS = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Beta" },
  { value: "c", label: "Gamma" },
];

describe("MultiSelect", () => {
  it("renders checkboxes instead of a native multi-select", () => {
    render(
      <MultiSelect options={OPTIONS} selected={[]} onChange={() => {}} />,
    );
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
  });

  it("adds a value when its checkbox is checked", () => {
    const onChange = vi.fn();
    render(
      <MultiSelect options={OPTIONS} selected={[]} onChange={onChange} />,
    );
    fireEvent.click(screen.getByLabelText("Beta"));
    expect(onChange).toHaveBeenCalledWith(["b"]);
  });

  it("renders selected values as chips and removes them", () => {
    const onChange = vi.fn();
    render(
      <MultiSelect options={OPTIONS} selected={["a"]} onChange={onChange} />,
    );
    fireEvent.click(screen.getByLabelText("Remove Alpha"));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("reorders selected chips with move controls when ordered", () => {
    const onChange = vi.fn();
    render(
      <MultiSelect
        ordered
        options={OPTIONS}
        selected={["a", "b"]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByLabelText("Move Beta up"));
    expect(onChange).toHaveBeenCalledWith(["b", "a"]);
  });

  it("disables additional choices after reaching max selections", () => {
    render(
      <MultiSelect
        options={OPTIONS}
        selected={["a", "b"]}
        onChange={() => {}}
        maxSelections={2}
      />,
    );
    expect(
      (screen.getByLabelText("Gamma") as HTMLInputElement).disabled,
    ).toBe(true);
  });
});
