"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { CheckCircle2, AlertCircle, Info, X, AlertTriangle } from "lucide-react";

type ToastVariant = "success" | "error" | "info" | "warning";

interface Toast {
  id: number;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (message: string, variant?: ToastVariant) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  warning: (message: string) => void;
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

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, variant: ToastVariant = "info") => {
      const id = Date.now() + Math.random();
      setToasts((prev) => [...prev, { id, message, variant }]);
      setTimeout(() => dismiss(id), 4000);
    },
    [dismiss],
  );

  const success = useCallback((message: string) => toast(message, "success"), [toast]);
  const error = useCallback((message: string) => toast(message, "error"), [toast]);
  const info = useCallback((message: string) => toast(message, "info"), [toast]);
  const warning = useCallback((message: string) => toast(message, "warning"), [toast]);

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
              className={`pointer-events-auto flex min-w-[280px] max-w-sm items-start gap-2.5 rounded-md border ${s.border} ${s.bg} bg-bg-elevated/95 backdrop-blur px-3.5 py-2.5 shadow-lg`}
              role="status"
            >
              <Icon size={16} className={`${s.iconColor} mt-0.5 shrink-0`} />
              <p className="flex-1 text-sm text-fg-primary">{t.message}</p>
              <button
                onClick={() => dismiss(t.id)}
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
