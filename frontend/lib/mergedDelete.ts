/**
 * Deleting an incident also deletes the incidents combined into it, so a
 * delete confirmation names them. Returns "" when nothing was combined.
 */
export function mergedDeleteNote(titles: string[], selected = 1): string {
  if (titles.length === 0) return "";
  const shown = titles.slice(0, 3).map((title) => `"${title}"`);
  const more = titles.length > 3 ? ` and ${titles.length - 3} more` : "";
  const noun = titles.length === 1 ? "incident" : "incidents";
  const into = selected === 1 ? "it" : "them";
  return ` This also deletes the ${titles.length} ${noun} combined into ${into}: ${shown.join(", ")}${more}.`;
}
