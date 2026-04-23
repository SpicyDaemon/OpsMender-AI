"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Modal } from "@/components/ui/Modal";

type Shortcut = {
  keys: string;
  label: string;
  target?: string;
};

const NAV_SHORTCUTS: Shortcut[] = [
  { keys: "g i", label: "Incidents", target: "/dashboard/incidents" },
  { keys: "g a", label: "Approvals", target: "/dashboard/approvals" },
  { keys: "g d", label: "Detectors", target: "/dashboard/detectors" },
  { keys: "g s", label: "Skills", target: "/dashboard/skills" },
  { keys: "g l", label: "Audit log", target: "/dashboard/audit" },
  { keys: "g c", label: "Config", target: "/dashboard/config" },
];

const OTHER_SHORTCUTS: Shortcut[] = [
  { keys: "?", label: "Show this shortcut help" },
  { keys: "Esc", label: "Close modal / dismiss dialog" },
];

const NAV_MAP: Record<string, string> = Object.fromEntries(
  NAV_SHORTCUTS.filter((s) => s.target).map((s) => [s.keys.split(" ")[1], s.target!]),
);

const G_WINDOW_MS = 800;

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
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;

      if (event.key === "?") {
        event.preventDefault();
        setHelpOpen((v) => !v);
        return;
      }

      if (event.key === "g" && !event.shiftKey) {
        event.preventDefault();
        // Wait for the next key within the window.
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), G_WINDOW_MS);
        window.addEventListener(
          "keydown",
          (next: KeyboardEvent) => {
            window.clearTimeout(timer);
            if (next.metaKey || next.ctrlKey || next.altKey) return;
            if (isTypingTarget(next.target)) return;
            const dest = NAV_MAP[next.key.toLowerCase()];
            if (dest) {
              next.preventDefault();
              router.push(dest);
            }
          },
          { once: true, signal: controller.signal },
        );
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
    window.addEventListener("aim:open-shortcuts", open);
    return () => window.removeEventListener("aim:open-shortcuts", open);
  }, []);

  return (
    <Modal open={helpOpen} onClose={() => setHelpOpen(false)} title="Keyboard shortcuts">
      <div className="space-y-5">
        <ShortcutSection title="Navigation" items={NAV_SHORTCUTS} />
        <ShortcutSection title="Other" items={OTHER_SHORTCUTS} />
        <p className="text-xs text-fg-muted">
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
      <ul className="space-y-1.5">
        {items.map((item) => (
          <li key={item.keys} className="flex items-center justify-between gap-4">
            <span className="text-sm text-fg-primary">{item.label}</span>
            <KeySequence keys={item.keys} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function KeySequence({ keys }: { keys: string }) {
  return (
    <span className="flex items-center gap-1">
      {keys.split(" ").map((part, idx) => (
        <KeyCap key={`${part}-${idx}`}>{part}</KeyCap>
      ))}
    </span>
  );
}

function KeyCap({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[1.5rem] items-center justify-center rounded-md border border-border-strong bg-bg-panel px-1.5 py-0.5 font-mono text-[11px] font-medium text-fg-primary">
      {children}
    </kbd>
  );
}
