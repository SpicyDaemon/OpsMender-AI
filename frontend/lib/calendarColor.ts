/**
 * Deterministic per-person color for calendar chips, so the same user always
 * reads as the same color across days/views. Tailwind utility strings.
 */

const USER_COLORS = [
  "bg-purple-500/20 border-purple-400 text-purple-100",
  "bg-emerald-500/20 border-emerald-400 text-emerald-100",
  "bg-sky-500/20 border-sky-400 text-sky-100",
  "bg-amber-500/20 border-amber-400 text-amber-100",
  "bg-rose-500/20 border-rose-400 text-rose-100",
  "bg-teal-500/20 border-teal-400 text-teal-100",
  "bg-indigo-500/20 border-indigo-400 text-indigo-100",
  "bg-lime-500/20 border-lime-400 text-lime-100",
  "bg-fuchsia-500/20 border-fuchsia-400 text-fuchsia-100",
  "bg-orange-500/20 border-orange-400 text-orange-100",
];

const NEUTRAL = "bg-bg-elevated border-border-subtle text-fg-muted";

export function personColor(userId: string | null | undefined): string {
  if (!userId) return NEUTRAL;
  let hash = 0;
  for (let i = 0; i < userId.length; i += 1) {
    hash = (hash * 31 + userId.charCodeAt(i)) | 0;
  }
  return USER_COLORS[Math.abs(hash) % USER_COLORS.length];
}
