"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/auth/AuthShell";
import { Button } from "@/components/ui/Button";
import { FormAlert } from "@/components/ui/Input";
import { confirmActionLink, getToken, previewActionLink } from "@/lib/api";
import { formatDateTime } from "@/lib/formatDate";
import { CONFIRM_PATH, forgetReturnPath, rememberReturnPath } from "@/lib/return-path";
import type { ActionLinkPreview, ActionLinkResult } from "@/lib/types";

const STATUS_LABELS: Record<string, string> = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
  merged: "Merged",
};

const STATE_TITLES: Record<string, string> = {
  invalid: "This link is not valid",
  used: "This link was already used",
  expired: "This link has expired",
  channel_off: "This link is turned off",
};

const linkClass = "font-medium text-accent-text hover:underline";

function incidentHref(id: string) {
  return `/dashboard/incidents/detail?id=${encodeURIComponent(id)}`;
}

function onHashChange(callback: () => void) {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

// The fragment exists only in the browser: null while the static page
// hydrates, so the first render matches the prerendered HTML.
function useHash(): string | null {
  return useSyncExternalStore(onHashChange, () => window.location.hash, () => null);
}

/**
 * Confirm page for a signed action link on a chat card (M1-99).
 *
 * Opening the link never acts, since link scanners follow links. The token
 * rides in the URL fragment (``#token=...``), so it never reaches a server
 * log; this page sends it in a POST body. Without a session the page goes to
 * sign-in and comes back here afterwards. The action runs only when the
 * signed-in person presses Confirm, under their own role.
 */
export default function ActionLinkConfirmPage() {
  const hash = useHash();
  const token =
    hash !== null && hash.startsWith("#token=")
      ? decodeURIComponent(hash.slice("#token=".length))
      : "";
  const [preview, setPreview] = useState<ActionLinkPreview | null>(null);
  const [result, setResult] = useState<ActionLinkResult | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) return;
    // Back here after sign-in, also when an old session turns out expired.
    rememberReturnPath(`${CONFIRM_PATH}#token=${encodeURIComponent(token)}`);
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    let cancelled = false;
    previewActionLink(token)
      .then((data) => {
        if (cancelled) return;
        forgetReturnPath();
        setPreview(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not check this link.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleConfirm() {
    setSubmitting(true);
    setError("");
    try {
      setResult(await confirmActionLink(token));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The action did not run.");
      // The link may have changed meanwhile (used by someone else, expired).
      previewActionLink(token).then(setPreview).catch(() => undefined);
    } finally {
      setSubmitting(false);
    }
  }

  const dashboardFooter = (
    <Link href="/dashboard" className={linkClass}>
      Go to the dashboard
    </Link>
  );

  if (result) {
    return (
      <AuthShell title="Done" description={result.message} eyebrow="" footer={dashboardFooter}>
        <Link href={incidentHref(result.incident_id)} className={linkClass}>
          Open the incident
        </Link>
      </AuthShell>
    );
  }

  const missing =
    hash !== null && !token ? "This link is missing its token. Open it again from the card." : "";
  const loadError = error || missing;

  if (!preview) {
    return (
      <AuthShell
        title={loadError ? "This link cannot be used" : "Checking the link…"}
        description=""
        eyebrow=""
        footer={loadError ? dashboardFooter : null}
      >
        {loadError ? (
          <FormAlert message={loadError} />
        ) : (
          <p className="text-sm text-fg-muted">Nothing happens until you confirm.</p>
        )}
      </AuthShell>
    );
  }

  if (preview.state !== "ready") {
    return (
      <AuthShell
        title={STATE_TITLES[preview.state] ?? "This link cannot be used"}
        description=""
        eyebrow=""
        footer={dashboardFooter}
      >
        <div className="space-y-3">
          <FormAlert message={preview.message ?? undefined} tone="info" />
          {preview.used_at && (
            <p className="text-sm text-fg-secondary">Used {formatDateTime(preview.used_at)}.</p>
          )}
          {preview.incident_id && (
            <Link href={incidentHref(preview.incident_id)} className={linkClass}>
              Open the incident
            </Link>
          )}
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={`${preview.action_label} this incident?`}
      description="Nothing happens until you confirm. The action is recorded as yours."
      eyebrow=""
      footer={dashboardFooter}
    >
      <div className="space-y-4">
        <dl className="space-y-2 text-sm">
          <div>
            <dt className="text-fg-muted">Incident</dt>
            <dd className="font-medium text-fg-primary">{preview.incident_title}</dd>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <dt className="text-fg-muted">Status</dt>
              <dd className="text-fg-primary">
                {STATUS_LABELS[preview.incident_status ?? ""] ?? preview.incident_status}
              </dd>
            </div>
            <div>
              <dt className="text-fg-muted">Severity</dt>
              <dd className="text-fg-primary">{preview.incident_severity ?? "Not set"}</dd>
            </div>
          </div>
          <div>
            <dt className="text-fg-muted">Posted by</dt>
            <dd className="text-fg-primary">{preview.channel_name}</dd>
          </div>
          {preview.expires_at && (
            <div>
              <dt className="text-fg-muted">Link expires</dt>
              <dd className="text-fg-primary">{formatDateTime(preview.expires_at)}</dd>
            </div>
          )}
        </dl>
        {!preview.can_act && (
          <FormAlert message="Your role cannot act on incidents. Ask an Operator or an Admin." />
        )}
        <FormAlert message={error} />
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={handleConfirm} disabled={!preview.can_act || submitting}>
            {submitting ? "Working…" : `Confirm: ${preview.action_label}`}
          </Button>
          {preview.incident_id && (
            <Link href={incidentHref(preview.incident_id)} className={linkClass}>
              Open the incident
            </Link>
          )}
        </div>
      </div>
    </AuthShell>
  );
}
