/**
 * Scroll a list's own scroll region to its end, never the page (M1-129).
 *
 * The session page kept its event stream and co-pilot chat scrolled to the
 * newest entry with `scrollIntoView` on a marker at the end of each list. On
 * a phone the panels sit in page flow, so that scrolled the page's main
 * column too and the page opened at the bottom of the stream: the title, the
 * Live pill, Stop and a pending approval were above the fold. The marker's
 * nearest `[data-scroll-region]` ancestor is the list's own scroll container
 * at every width, so only it moves.
 */
export const SCROLL_REGION_ATTRIBUTE = "data-scroll-region";

export function scrollRegionToEnd(marker: Element | null | undefined): void {
  const region = marker?.closest<HTMLElement>(`[${SCROLL_REGION_ATTRIBUTE}]`);
  if (!region) return;
  if (typeof region.scrollTo === "function") {
    region.scrollTo({ top: region.scrollHeight, behavior: "smooth" });
  } else {
    region.scrollTop = region.scrollHeight;
  }
}
