"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  maxWidth?: string;
  // Optional content rendered in the modal header to the left of the
  // close button (e.g. a Templates toggle button on the MCP modal).
  headerExtra?: ReactNode;
}

export function Modal({
  open,
  onClose,
  title,
  children,
  maxWidth = "max-w-lg",
  headerExtra,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;

    const previousFocus = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const focusable = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((element) => element.getClientRects().length > 0);
    (focusable()[0] ?? panel)?.focus();

    const handler = (e: KeyboardEvent) => {
      const dialogs = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
      if (dialogs[dialogs.length - 1] !== panel) return;
      if (e.key === "Escape") onCloseRef.current();
      if (e.key !== "Tab") return;
      const items = focusable();
      if (!items.length) {
        e.preventDefault();
        panel?.focus();
      } else if (!panel?.contains(document.activeElement)) {
        e.preventDefault();
        (e.shiftKey ? items[items.length - 1] : items[0]).focus();
      } else if (e.shiftKey && document.activeElement === items[0]) {
        e.preventDefault();
        items[items.length - 1].focus();
      } else if (!e.shiftKey && document.activeElement === items[items.length - 1]) {
        e.preventDefault();
        items[0].focus();
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      previousFocus?.focus();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto py-6">
      <div
        className="ops-modal-backdrop absolute inset-0 bg-black/70"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`ops-modal-panel relative z-10 my-auto flex max-h-[calc(100vh-3rem)] w-full ${maxWidth} mx-4 flex-col rounded-lg bg-bg-elevated border border-border-strong shadow-2xl`}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border-subtle px-5 py-3.5">
          <h2 id={titleId} className="text-sm font-semibold text-fg-primary">{title}</h2>
          <div className="flex items-center gap-2">
            {headerExtra}
            <button
              aria-label={`Close ${title}`}
              title={`Close ${title}`}
              onClick={onClose}
              className="rounded-md p-1 text-fg-muted hover:bg-bg-hover hover:text-fg-primary transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        </div>
        <div
          data-testid="modal-body"
          className="min-h-0 flex-1 overflow-y-auto px-5 py-4"
        >
          {children}
        </div>
      </div>
    </div>
  );
}
