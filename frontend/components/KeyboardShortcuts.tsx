"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Modal } from "@/components/ui/Modal";

type Shortcut = {
  keys: string[];
  label: string;
  target?: string;
};

const ALT_LABEL =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.platform)
    ? "Option"
    : "Alt";

const NAV_SHORTCUTS: Shortcut[] = [
  { keys: [ALT_LABEL, "I"], label: "Incidents", target: "/dashboard/incidents" },
  { keys: [ALT_LABEL, "A"], label: "Approvals", target: "/dashboard/approvals" },
  { keys: [ALT_LABEL, "D"], label: "Environment Scans", target: "/dashboard/scans" },
  { keys: [ALT_LABEL, "S"], label: "Skills", target: "/dashboard/skills" },
  { keys: [ALT_LABEL, "L"], label: "Activity", target: "/dashboard/activity" },
  { keys: [ALT_LABEL, "C"], label: "Config", target: "/dashboard/config" },
];

const OTHER_SHORTCUTS: Shortcut[] = [
  { keys: [ALT_LABEL, "/"], label: "Show this shortcut help" },
  { keys: ["?"], label: "Show this shortcut help (alt)" },
  { keys: ["Esc"], label: "Close modal / dismiss dialog" },
];

// Map by KeyboardEvent.code so macOS Option-modified characters still match.
const NAV_CODE_MAP: Record<string, string> = {
  KeyI: "/dashboard/incidents",
  KeyA: "/dashboard/approvals",
  KeyD: "/dashboard/scans",
  KeyS: "/dashboard/skills",
  KeyL: "/dashboard/activity",
  KeyC: "/dashboard/config",
};

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}

export function KeyboardShortcuts() {
  const router = useRouter();
  const [helpOpen, setHelpOpen] = useState(false);

  const handleKey = useCallback(
    (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (isTypingTarget(event.target)) return;

      // Help: `?` (Shift+/) or Alt+/
      if (
        !event.metaKey &&
        !event.ctrlKey &&
        (event.key === "?" || (event.altKey && event.code === "Slash"))
      ) {
        event.preventDefault();
        setHelpOpen((v) => !v);
        return;
      }

      // Navigation: Alt + letter (no Ctrl/Meta).
      if (event.altKey && !event.ctrlKey && !event.metaKey) {
        const dest = NAV_CODE_MAP[event.code];
        if (dest) {
          event.preventDefault();
          router.push(dest);
        }
      }
    },
    [router],
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [handleKey]);

  useEffect(() => {
    const open = () => setHelpOpen(true);
    window.addEventListener("opsmender:open-shortcuts", open);
    return () => window.removeEventListener("opsmender:open-shortcuts", open);
  }, []);

  return (
    <Modal open={helpOpen} onClose={() => setHelpOpen(false)} title="Keyboard shortcuts">
      <div className="space-y-6">
        <ShortcutSection title="Navigation" items={NAV_SHORTCUTS} />
        <ShortcutSection title="Other" items={OTHER_SHORTCUTS} />
        <p className="border-t border-border-subtle pt-4 text-xs leading-5 text-fg-muted">
          Press <KeyCap>?</KeyCap> any time to reopen this panel. Shortcuts are
          ignored while typing in an input or textarea.
        </p>
      </div>
    </Modal>
  );
}

function ShortcutSection({
  title,
  items,
}: {
  title: string;
  items: Shortcut[];
}) {
  return (
    <div>
      <p className="mb-2 font-mono text-[11px] uppercase tracking-wider text-fg-muted">
        {title}
      </p>
      <ul className="divide-y divide-border-subtle/60">
        {items.map((item) => (
          <li key={item.keys.join("-")} className="flex items-center justify-between gap-4 py-2">
            <span className="text-sm text-fg-primary">{item.label}</span>
            <KeyCombo keys={item.keys} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function KeyCombo({ keys }: { keys: string[] }) {
  return (
    <span
      className="flex min-w-[7rem] items-center justify-end gap-1.5"
      aria-label={keys.join(" plus ")}
    >
      {keys.map((part, idx) => (
        <span key={`${part}-${idx}`} className="flex items-center gap-1.5">
          {idx > 0 && (
            <span className="text-[10px] font-medium uppercase tracking-wide text-fg-muted">
              +
            </span>
          )}
          <KeyCap>{part}</KeyCap>
        </span>
      ))}
    </span>
  );
}

function KeyCap({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[1.75rem] items-center justify-center rounded-md border border-border-strong bg-bg-panel px-2 py-1 font-mono text-[11px] font-semibold leading-none text-fg-primary shadow-sm">
      {children}
    </kbd>
  );
}
