/**
 * M1-129 (MB-01): keeping a list scrolled to its newest entry moves the list's
 * own scroll region, never the window or the page's main column.
 *
 * In the dashboard the window never scrolls (the layout is `h-screen
 * overflow-hidden`); the page scroller is `main#main-content`, and that is the
 * element the old `scrollIntoView` moved on phones. Every case below checks
 * that element as well as the window.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { scrollRegionToEnd } from "@/lib/scrollRegion";

function build({ withRegion = true }: { withRegion?: boolean } = {}) {
  document.body.innerHTML = withRegion
    ? `
    <main id="main-content">
      <div data-scroll-region class="overflow-y-auto">
        <div class="list"><div class="entry"></div><div id="marker"></div></div>
      </div>
    </main>`
    : `
    <main id="main-content">
      <div class="list"><div class="entry"></div><div id="marker"></div></div>
    </main>`;
  const main = document.getElementById("main-content")!;
  const region = document.querySelector<HTMLElement>("[data-scroll-region]");
  const marker = document.getElementById("marker")!;
  Object.defineProperty(main, "scrollHeight", { configurable: true, value: 4000 });
  const mainScrollTo = vi.fn();
  main.scrollTo = mainScrollTo as unknown as typeof main.scrollTo;
  const regionScrollTo = vi.fn();
  if (region) {
    Object.defineProperty(region, "scrollHeight", { configurable: true, value: 2124 });
    region.scrollTo = regionScrollTo as unknown as typeof region.scrollTo;
  }
  const windowScrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  const intoView = vi.fn();
  marker.scrollIntoView = intoView as unknown as typeof marker.scrollIntoView;
  return { main, region, marker, mainScrollTo, regionScrollTo, windowScrollTo, intoView };
}

function expectPageUntouched(ctx: ReturnType<typeof build>) {
  expect(ctx.windowScrollTo).not.toHaveBeenCalled();
  expect(ctx.mainScrollTo).not.toHaveBeenCalled();
  expect(ctx.main.scrollTop).toBe(0);
  expect(ctx.intoView).not.toHaveBeenCalled();
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("scrollRegionToEnd", () => {
  it("scrolls the marker's scroll region to its end and nothing else", () => {
    const ctx = build();

    scrollRegionToEnd(ctx.marker);

    expect(ctx.regionScrollTo).toHaveBeenCalledWith({ top: 2124, behavior: "smooth" });
    expectPageUntouched(ctx);
  });

  it("falls back to scrollTop where scrollTo is missing", () => {
    const ctx = build();
    (ctx.region as { scrollTo?: unknown }).scrollTo = undefined;

    scrollRegionToEnd(ctx.marker);

    expect(ctx.region!.scrollTop).toBe(2124);
    expectPageUntouched(ctx);
  });

  it("leaves the page alone when the marker sits in the page but in no region", () => {
    // A marker inside main#main-content with no region between them: the
    // helper must not fall back to the page scroller, which is the bug.
    const ctx = build({ withRegion: false });

    scrollRegionToEnd(ctx.marker);

    expectPageUntouched(ctx);
  });

  it("does nothing without a marker", () => {
    const ctx = build();

    scrollRegionToEnd(null);
    scrollRegionToEnd(undefined);

    expect(ctx.regionScrollTo).not.toHaveBeenCalled();
    expectPageUntouched(ctx);
  });
});
