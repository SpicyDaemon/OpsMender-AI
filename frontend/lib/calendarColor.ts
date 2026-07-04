/**
 * Deterministic per-person color for calendar chips, so the same user always
 * reads as the same color across days/views. Tailwind utility strings.
 */

// Each entry pairs a dark-theme treatment (light text on a translucent tint,
// the default) with a `light:` override (dark text on a light tint) so chips
// stay >=4.5:1 in both themes. The app flips themes via `[data-theme]`, which
// the `light:` custom-variant (see globals.css) keys off — not `dark:`/media.
const USER_COLORS = [
  "bg-purple-500/20 border-purple-400 text-purple-100 light:bg-purple-500/15 light:border-purple-500 light:text-purple-900",
  "bg-emerald-500/20 border-emerald-400 text-emerald-100 light:bg-emerald-500/15 light:border-emerald-600 light:text-emerald-900",
  "bg-sky-500/20 border-sky-400 text-sky-100 light:bg-sky-500/15 light:border-sky-600 light:text-sky-900",
  "bg-amber-500/20 border-amber-400 text-amber-100 light:bg-amber-500/15 light:border-amber-600 light:text-amber-900",
  "bg-rose-500/20 border-rose-400 text-rose-100 light:bg-rose-500/15 light:border-rose-500 light:text-rose-900",
  "bg-teal-500/20 border-teal-400 text-teal-100 light:bg-teal-500/15 light:border-teal-600 light:text-teal-900",
  "bg-indigo-500/20 border-indigo-400 text-indigo-100 light:bg-indigo-500/15 light:border-indigo-500 light:text-indigo-900",
  "bg-lime-500/20 border-lime-400 text-lime-100 light:bg-lime-500/15 light:border-lime-600 light:text-lime-900",
  "bg-fuchsia-500/20 border-fuchsia-400 text-fuchsia-100 light:bg-fuchsia-500/15 light:border-fuchsia-500 light:text-fuchsia-900",
  "bg-orange-500/20 border-orange-400 text-orange-100 light:bg-orange-500/15 light:border-orange-600 light:text-orange-900",
];

// Tokens flip by theme already, so the neutral chip needs no light override.
const NEUTRAL = "bg-bg-elevated border-border-subtle text-fg-muted";

export function personColor(userId: string | null | undefined): string {
  if (!userId) return NEUTRAL;
  let hash = 0;
  for (let i = 0; i < userId.length; i += 1) {
    hash = (hash * 31 + userId.charCodeAt(i)) | 0;
  }
  return USER_COLORS[Math.abs(hash) % USER_COLORS.length];
}
