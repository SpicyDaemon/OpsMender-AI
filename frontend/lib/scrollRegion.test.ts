/**
 * M1-129 (MB-01): keeping a list scrolled to its newest entry moves the list's
 * own scroll region, never the window or the page's main column.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { scrollRegionToEnd } from "@/lib/scrollRegion";

function build() {
  document.body.innerHTML = `
    <main id="main-content">
      <div data-scroll-region class="overflow-y-auto">
        <div class="list"><div class="entry"></div><div id="marker"></div></div>
      </div>
    </main>`;
  const region = document.querySelector<HTMLElement>("[data-scroll-region]")!;
  const marker = document.getElementById("marker")!;
  Object.defineProperty(region, "scrollHeight", { configurable: true, value: 2124 });
  const regionScrollTo = vi.fn();
  region.scrollTo = regionScrollTo as unknown as typeof region.scrollTo;
  const windowScrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  const intoView = vi.fn();
  marker.scrollIntoView = intoView as unknown as typeof marker.scrollIntoView;
  return { region, marker, regionScrollTo, windowScrollTo, intoView };
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("scrollRegionToEnd", () => {
  it("scrolls the marker's scroll region to its end and nothing else", () => {
    const { regionScrollTo, windowScrollTo, intoView, marker } = build();

    scrollRegionToEnd(marker);

    expect(regionScrollTo).toHaveBeenCalledWith({ top: 2124, behavior: "smooth" });
    expect(windowScrollTo).not.toHaveBeenCalled();
    expect(intoView).not.toHaveBeenCalled();
  });

  it("falls back to scrollTop where scrollTo is missing", () => {
    const { region, marker, windowScrollTo } = build();
    (region as { scrollTo?: unknown }).scrollTo = undefined;

    scrollRegionToEnd(marker);

    expect(region.scrollTop).toBe(2124);
    expect(windowScrollTo).not.toHaveBeenCalled();
  });

  it("does nothing without a marker or a region", () => {
    const { windowScrollTo } = build();
    const loose = document.createElement("div");

    scrollRegionToEnd(null);
    scrollRegionToEnd(loose);

    expect(windowScrollTo).not.toHaveBeenCalled();
  });
});
