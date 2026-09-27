"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { fullIntakeUrl } from "@/lib/intake";

/**
 * A service's intake URL carries the secret that lets a monitor post alerts,
 * so only its hash is stored (S-108). The full URL is shown once, on create
 * and on rotate; everywhere else shows the masked hint.
 */
export function IntakeUrlHint({
  hint,
  publicBaseUrl,
}: {
  hint: string | null | undefined;
  publicBaseUrl: string | null;
}) {
  const full = fullIntakeUrl(hint, publicBaseUrl);
  if (!full) {
    return <span className="text-[11px] text-fg-muted">No URL yet</span>;
  }
  return (
    <span
      className="block max-w-[22rem] truncate font-mono text-[11px] text-fg-secondary"
      title="Shown in full only when it's created. Rotate it from the service to get a new one."
    >
      {full}
    </span>
  );
}

export function IntakeUrlOnceDialog({
  shown,
  publicBaseUrl,
  onClose,
}: {
  shown: { name: string; url: string } | null;
  publicBaseUrl: string | null;
  onClose: () => void;
}) {
  const full = shown ? (fullIntakeUrl(shown.url, publicBaseUrl) ?? shown.url) : "";
  return (
    <Modal
      open={shown !== null}
      onClose={onClose}
      title={shown ? `Intake URL for ${shown.name}` : "Intake URL"}
    >
      {shown && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2">
            <span className="min-w-0 flex-1 break-all font-mono text-xs text-fg-primary">
              {full}
            </span>
            <CopyButton value={full} label="intake URL" />
          </div>
          <p className="text-xs text-fg-muted">
            Copy it now: it won&apos;t be shown again. Point your alerting
            systems here. If you lose it, rotate it from the service to get a
            new one.
          </p>
          <div className="flex justify-end">
            <Button onClick={onClose}>Done</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** Inline copy-to-clipboard button for the one-time intake URL. */
export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      size="sm"
      title={`Copy ${label.toLowerCase()}`}
      aria-label={`Copy ${label.toLowerCase()}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable - non-fatal */
        }
      }}
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </Button>
  );
}
