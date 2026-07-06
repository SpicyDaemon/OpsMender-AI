"use client";

import Link from "next/link";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { CheckCircle2, AlertCircle, Info, X, AlertTriangle } from "lucide-react";

type ToastVariant = "success" | "error" | "info" | "warning";

interface Toast {
  id: number;
  message: string;
  variant: ToastVariant;
  action?: ToastAction;
  closing?: boolean;
}

interface ToastAction {
  label: string;
  href: string;
}

interface ToastContextValue {
  toast: (message: string, variant?: ToastVariant, action?: ToastAction) => void;
  success: (message: string, action?: ToastAction) => void;
  error: (message: string, action?: ToastAction) => void;
  info: (message: string, action?: ToastAction) => void;
  warning: (message: string, action?: ToastAction) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const STYLES: Record<ToastVariant, { bg: string; border: string; icon: typeof CheckCircle2; iconColor: string }> = {
  success: {
    bg: "bg-status-low-bg",
    border: "border-status-low-border",
    icon: CheckCircle2,
    iconColor: "text-status-low",
  },
  error: {
    bg: "bg-status-critical-bg",
    border: "border-status-critical-border",
    icon: AlertCircle,
    iconColor: "text-status-critical",
  },
  info: {
    bg: "bg-status-info-bg",
    border: "border-status-info-border",
    icon: Info,
    iconColor: "text-status-info",
  },
  warning: {
    bg: "bg-status-high-bg",
    border: "border-status-high-border",
    icon: AlertTriangle,
    iconColor: "text-status-high",
  },
};

const AUTO_DISMISS_MS = 4000;
const TOAST_EXIT_MS = 180;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef(new Set<number>());

  const schedule = useCallback((callback: () => void, delay: number) => {
    const timer = window.setTimeout(() => {
      timers.current.delete(timer);
      callback();
    }, delay);
    timers.current.add(timer);
  }, []);

  useEffect(
    () => () => {
      timers.current.forEach((timer) => window.clearTimeout(timer));
      timers.current.clear();
    },
    [],
  );

  const remove = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) =>
      prev.map((t) => (t.id === id ? { ...t, closing: true } : t)),
    );
    schedule(() => remove(id), TOAST_EXIT_MS);
  }, [remove, schedule]);

  const toast = useCallback(
    (message: string, variant: ToastVariant = "info", action?: ToastAction) => {
      const id = Date.now() + Math.random();
      setToasts((prev) => [...prev, { id, message, variant, action }]);
      schedule(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss, schedule],
  );

  const success = useCallback(
    (message: string, action?: ToastAction) => toast(message, "success", action),
    [toast],
  );
  const error = useCallback(
    (message: string, action?: ToastAction) => toast(message, "error", action),
    [toast],
  );
  const info = useCallback(
    (message: string, action?: ToastAction) => toast(message, "info", action),
    [toast],
  );
  const warning = useCallback(
    (message: string, action?: ToastAction) => toast(message, "warning", action),
    [toast],
  );

  const value: ToastContextValue = useMemo(
    () => ({
      toast,
      success,
      error,
      info,
      warning,
    }),
    [toast, success, error, info, warning],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex flex-col gap-2">
        {toasts.map((t) => {
          const s = STYLES[t.variant];
          const Icon = s.icon;
          return (
            <div
              key={t.id}
              className={`ops-toast ${
                t.closing ? "ops-toast--closing" : ""
              } pointer-events-auto flex min-w-[280px] max-w-sm items-start gap-2.5 rounded-md border ${s.border} ${s.bg} bg-bg-elevated/95 backdrop-blur px-3.5 py-2.5 shadow-lg`}
              role="status"
            >
              <Icon size={16} className={`${s.iconColor} mt-0.5 shrink-0`} />
              <div className="flex-1">
                <p className="text-sm text-fg-primary">{t.message}</p>
                {t.action && (
                  <Link
                    href={t.action.href}
                    className="mt-1 inline-flex text-xs font-medium text-accent-text hover:underline"
                    onClick={() => dismiss(t.id)}
                  >
                    {t.action.label}
                  </Link>
                )}
              </div>
              <button
                onClick={() => dismiss(t.id)}
                aria-label="Dismiss notification"
                className="shrink-0 rounded p-0.5 text-fg-muted hover:text-fg-primary"
              >
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
