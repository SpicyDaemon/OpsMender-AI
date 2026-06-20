"use client";

import type { UserResponse } from "@/lib/types";

/**
 * Generated initials avatar. v1 keeps it storage-free: a colored rounded
 * square with the user's initials. `avatar_color` (a palette key) picks the
 * background; when unset it's derived deterministically from the username so
 * each user gets a stable color.
 */

export const AVATAR_PALETTE: Record<string, string> = {
  violet: "#7c3aed",
  blue: "#2563eb",
  green: "#16a34a",
  amber: "#d97706",
  rose: "#e11d48",
  cyan: "#0891b2",
  slate: "#475569",
  pink: "#db2777",
};

export const AVATAR_COLOR_KEYS = Object.keys(AVATAR_PALETTE);

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

export function avatarColorFor(user: {
  username: string | null;
  email?: string | null;
  avatar_color?: string | null;
}): string {
  if (user.avatar_color && AVATAR_PALETTE[user.avatar_color]) {
    return AVATAR_PALETTE[user.avatar_color];
  }
  const identity = user.username ?? user.email ?? "user";
  const key = AVATAR_COLOR_KEYS[hashString(identity) % AVATAR_COLOR_KEYS.length];
  return AVATAR_PALETTE[key];
}

export function userInitials(user: {
  username: string | null;
  email?: string | null;
  first_name?: string | null;
  last_name?: string | null;
}): string {
  const f = (user.first_name ?? "").trim();
  const l = (user.last_name ?? "").trim();
  if (f || l) return `${f.slice(0, 1)}${l.slice(0, 1)}`.toUpperCase() || f.slice(0, 2).toUpperCase();
  return (user.username ?? user.email ?? "U").slice(0, 2).toUpperCase();
}

export function userDisplayName(user: {
  username: string | null;
  email?: string | null;
  first_name?: string | null;
  last_name?: string | null;
}): string {
  const full = `${(user.first_name ?? "").trim()} ${(user.last_name ?? "").trim()}`.trim();
  return full || user.username || user.email || "User";
}

export function Avatar({
  user,
  size = 28,
  className = "",
}: {
  user: {
    username: UserResponse["username"] | null;
    email?: string | null;
    first_name?: string | null;
    last_name?: string | null;
    avatar_color?: string | null;
  };
  size?: number;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center rounded-md font-semibold text-white ${className}`}
      style={{
        width: size,
        height: size,
        backgroundColor: avatarColorFor(user),
        fontSize: Math.round(size * 0.4),
      }}
      aria-hidden="true"
    >
      {userInitials(user)}
    </span>
  );
}
