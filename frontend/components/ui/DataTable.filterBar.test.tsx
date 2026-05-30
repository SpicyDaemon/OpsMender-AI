import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";

interface Row {
  id: string;
  name: string;
  provider: string;
}

const ROWS: Row[] = [
  { id: "1", name: "alpha", provider: "anthropic" },
  { id: "2", name: "beta", provider: "openai" },
  { id: "3", name: "gamma", provider: "azure" },
];

const COLUMNS: DataTableColumn<Row>[] = [
  { id: "name", label: "Name", accessor: (r) => r.name, searchable: true },
  {
    id: "provider",
    label: "Provider",
    accessor: (r) => r.provider,
    filterChips: {
      options: [
        { value: "anthropic", label: "Anthropic" },
        { value: "openai", label: "OpenAI" },
        { value: "azure", label: "Azure" },
      ],
      valueOf: (r) => r.provider,
    },
  },
];

function renderTable() {
  return render(
    <DataTable
      rows={ROWS}
      columns={COLUMNS}
      rowKey={(r) => r.id}
      filterBar
      searchPlaceholder="Search…"
      toolbarRight={<button type="button">New model</button>}
    />,
  );
}

describe("DataTable filterBar layout", () => {
  it("renders a multi-select checkbox dropdown for each filterChips column", () => {
    renderTable();
    // Dropdown trigger shows the column label when nothing is selected.
    expect(screen.getByRole("button", { name: /All Provider/i })).toBeTruthy();
    // The toolbarRight action lives in the filter row.
    expect(screen.getByRole("button", { name: /New model/i })).toBeTruthy();
  });

  it("filters with OR semantics within a dimension via checkboxes", () => {
    renderTable();
    // All three rows visible initially (mobile + desktop layouts both in DOM).
    expect(screen.getAllByText("alpha").length).toBeGreaterThan(0);
    expect(screen.getAllByText("beta").length).toBeGreaterThan(0);
    expect(screen.getAllByText("gamma").length).toBeGreaterThan(0);

    // Open the Provider dropdown and select Anthropic + OpenAI (OR).
    fireEvent.click(screen.getByRole("button", { name: /All Provider/i }));
    fireEvent.click(screen.getByLabelText("Anthropic"));
    fireEvent.click(screen.getByLabelText("OpenAI"));

    // alpha (anthropic) + beta (openai) match; gamma (azure) is filtered out.
    expect(screen.getAllByText("alpha").length).toBeGreaterThan(0);
    expect(screen.getAllByText("beta").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("gamma").length).toBe(0);

    // The trigger reflects the selected count.
    expect(screen.getByRole("button", { name: /Provider · 2/i })).toBeTruthy();
  });
});
