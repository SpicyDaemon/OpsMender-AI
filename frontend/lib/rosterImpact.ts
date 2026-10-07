import type { RosterImpactItem } from "@/lib/types";

/**
 * The sentence a demotion or deactivation confirmation adds: the Rosters the
 * person leaves and who takes each current shift. "" when they're on none.
 */
export function rosterImpactNote(items: RosterImpactItem[]): string {
  if (items.length === 0) return "";
  const rosters = items.map((item) =>
    item.on_current_shift
      ? `${item.roster_name} (${
          item.current_shift_taken_by
            ? `${item.current_shift_taken_by} takes the current shift`
            : "nobody else can take the current shift"
        })`
      : item.roster_name,
  );
  return ` They leave ${items.length === 1 ? "this Roster" : "these Rosters"}: ${rosters.join(", ")}. The others keep their shifts.`;
}
