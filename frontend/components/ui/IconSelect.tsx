"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { ChevronDown } from "lucide-react";

export type IconSelectOption = {
  value: string;
  label: string;
  icon?: ReactNode;
};

const TRIGGER =
  "flex w-full items-center gap-2 rounded-md border border-border-strong bg-bg-input px-3 py-2 text-sm text-fg-primary transition-colors focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent";

/**
 * A select-style dropdown that can render an icon beside each option — which a
 * native <select> cannot. Keyboard-navigable (arrows, Home/End, typeahead,
 * Enter/Escape) with click-outside dismissal.
 */
export function IconSelect({
  id,
  value,
  options,
  onChange,
  disabled = false,
  placeholder = "Select…",
  className = "",
}: {
  id?: string;
  value: string;
  options: IconSelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);
  const typeahead = useRef<{ buffer: string; at: number }>({ buffer: "", at: 0 });
  const listId = useId();

  const selected = options.find((o) => o.value === value) ?? null;
  const selectedIndex = options.findIndex((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  useEffect(() => {
    if (open) setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
  }, [open, selectedIndex]);

  useEffect(() => {
    if (!open || activeIndex < 0) return;
    const node = listRef.current?.children[activeIndex] as HTMLElement | undefined;
    // scrollIntoView is unimplemented in jsdom (tests) — guard it.
    if (typeof node?.scrollIntoView === "function") node.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex]);

  function commit(index: number) {
    const opt = options[index];
    if (opt) {
      onChange(opt.value);
      setOpen(false);
    }
  }

  function onKeyDown(e: ReactKeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;
    if (!open) {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    switch (e.key) {
      case "Escape":
        e.preventDefault();
        setOpen(false);
        break;
      case "ArrowDown":
        e.preventDefault();
        setActiveIndex((i) => Math.min(options.length - 1, i + 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        setActiveIndex((i) => Math.max(0, i - 1));
        break;
      case "Home":
        e.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        e.preventDefault();
        setActiveIndex(options.length - 1);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        commit(activeIndex);
        break;
      default:
        if (e.key.length === 1) {
          const now = Date.now();
          const ta = typeahead.current;
          ta.buffer = now - ta.at > 600 ? e.key : ta.buffer + e.key;
          ta.at = now;
          const q = ta.buffer.toLowerCase();
          const match = options.findIndex((o) => o.label.toLowerCase().startsWith(q));
          if (match >= 0) setActiveIndex(match);
        }
    }
  }

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        id={id}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => !disabled && setOpen((o) => !o)}
        onKeyDown={onKeyDown}
        className={`${TRIGGER} ${disabled ? "cursor-not-allowed bg-bg-hover opacity-60" : "cursor-pointer"}`}
      >
        {selected?.icon}
        <span className={`flex-1 truncate text-left ${selected ? "" : "text-fg-muted"}`}>
          {selected?.label ?? placeholder}
        </span>
        <ChevronDown size={16} className="shrink-0 text-fg-muted" />
      </button>
      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-border-subtle bg-bg-panel py-1 shadow-lg"
        >
          {options.map((opt, i) => {
            const isSelected = opt.value === value;
            const isActive = i === activeIndex;
            return (
              <li
                key={opt.value}
                role="option"
                data-value={opt.value}
                aria-selected={isSelected}
                onMouseEnter={() => setActiveIndex(i)}
                onClick={() => commit(i)}
                className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                  isActive ? "bg-bg-hover" : ""
                } ${isSelected ? "text-accent-text" : "text-fg-primary"}`}
              >
                {opt.icon}
                <span className="truncate">{opt.label}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
