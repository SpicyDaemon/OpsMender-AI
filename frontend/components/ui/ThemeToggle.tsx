"use client";

import { Laptop, Moon, Sun } from "lucide-react";
import { useTheme, type ThemeMode } from "@/context/theme";

const OPTIONS: Array<{
  mode: ThemeMode;
  label: string;
  icon: typeof Sun;
}> = [
  { mode: "system", label: "System", icon: Laptop },
  { mode: "light", label: "Light", icon: Sun },
  { mode: "dark", label: "Dark", icon: Moon },
];

export function ThemeToggle({
  compact = false,
  full = false,
}: {
  compact?: boolean;
  /** Stretch to fill the container with evenly-sized segments. */
  full?: boolean;
}) {
  const { mode, setMode } = useTheme();

  return (
    <div
      className={`items-center rounded-md border border-border-subtle bg-bg-input p-1 ${
        full ? "flex w-full gap-1" : "inline-flex"
      } ${!full && compact ? "gap-1" : ""} ${!full && !compact ? "gap-1.5" : ""}`}
    >
      {OPTIONS.map(({ mode: option, label, icon: Icon }) => {
        const active = mode === option;
        return (
          <button
            key={option}
            type="button"
            onClick={() => setMode(option)}
            aria-pressed={active}
            title={label}
            className={`inline-flex items-center justify-center whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium transition-colors ${
              full ? "min-w-0 flex-1" : ""
            } ${
              active
                ? "bg-accent text-white"
                : "text-fg-secondary hover:bg-bg-hover hover:text-fg-primary"
            }`}
          >
            <Icon size={14} />
            {!compact && <span className="ml-1.5">{label}</span>}
          </button>
        );
      })}
    </div>
  );
}
