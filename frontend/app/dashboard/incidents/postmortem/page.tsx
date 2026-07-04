"use client";

/**
 * Sprint 61 Step 4 — postmortem authoring surface.
 *
 * Operator-facing markdown editor for an incident's postmortem. The
 * backend stores a single markdown blob; this page provides a textarea
 * scaffolded with the canonical section headings (Summary · Impact ·
 * Timeline · Root cause · Resolution · Lessons learned · Memory
 * candidates) returned by the API so the frontend doesn't hardcode the
 * structure.
 */

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Bot, Brain, Check, Circle, Download, Eye, Pencil, Save, ScrollText, Sparkles, Trash2 } from "lucide-react";
import {
  draftIncidentPostmortemFromSessions,
  extractPostmortemMemoryCandidates,
  getIncident,
  getIncidentPostmortem,
  putIncidentPostmortem,
} from "@/lib/api";
import { useDashboardNavigation } from "@/lib/use-dashboard-navigation";
import { responderDisplay } from "@/lib/responder";
import type {
  IncidentPostmortemResponse,
  IncidentResponse,
} from "@/lib/types";

function buildDraftFromIncident(incident: IncidentResponse): string {
  const now = new Date().toISOString();
  const created = incident.created_at
    ? new Date(incident.created_at).toISOString()
    : "(unknown)";
  const updated = incident.updated_at
    ? new Date(incident.updated_at).toISOString()
    : "(unknown)";
  const severity = incident.severity ?? "unknown";
  const status = incident.status ?? "unknown";
  const responder = responderDisplay(incident);
  const service = incident.service_name ?? incident.external_source ?? "(unknown)";
  const team = incident.team_name ?? "(unknown)";

  return [
    `# Postmortem: ${incident.title}`,
    ``,
    `**Incident:** ${incident.id.slice(0, 8)}`,
    `**Severity:** ${severity}  **Status:** ${status}`,
    `**Service:** ${service}  **Team:** ${team}`,
    `**Responder:** ${responder.text}`,
    `**Created:** ${created}  **Last updated:** ${updated}`,
    `**Draft generated:** ${now}`,
    ``,
    `---`,
    ``,
    `## Summary`,
    ``,
    `<!-- What happened, in one paragraph. -->`,
    ``,
    incident.description ? incident.description.trim() : `_Summarize what happened here._`,
    ``,
    `## Impact`,
    ``,
    `<!-- Who was affected, for how long, how badly. -->`,
    ``,
    `_Describe customer or system impact._`,
    ``,
    `## Timeline`,
    ``,
    `<!-- Key moments with UTC timestamps. -->`,
    ``,
    `| Time (UTC) | Event |`,
    `|---|---|`,
    `| ${created} | Incident opened (${severity}) |`,
    `| ${updated} | Last updated |`,
    `| _..._ | _Add key events here_ |`,
    ``,
    `## Root Cause`,
    ``,
    `<!-- What caused the incident. -->`,
    ``,
    `_Describe the root cause._`,
    ``,
    `## Resolution`,
    ``,
    `<!-- What fixed it. -->`,
    ``,
    `_Describe how the incident was resolved._`,
    ``,
    `## Lessons Learned`,
    ``,
    `<!-- What the team should do differently. -->`,
    ``,
    `- `,
    ``,
    `## Memory Candidates`,
    ``,
    `<!-- Short, durable lessons for the AI recall system. One bullet per memory. -->`,
    ``,
    `- `,
  ].join(`\n`);
}
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { DetailSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

const SECTIONS = [
  { heading: "Summary", hint: "What happened, in one paragraph." },
  { heading: "Impact", hint: "Who was affected, for how long, how badly." },
  { heading: "Timeline", hint: "Key moments with UTC timestamps." },
  { heading: "Root cause", hint: "The underlying technical cause." },
  { heading: "Resolution", hint: "What you changed and what's still in flight." },
  { heading: "Lessons learned", hint: "What worked, what didn't." },
  {
    heading: "Memory candidates",
    hint: "Durable lessons to save into OpsMender memory.",
  },
];

/** Body of a `## <heading>` section, up to the next `##` heading. */
function sectionBody(md: string, heading: string): string {
  const lines = md.split("\n");
  const headRe = new RegExp(
    `^#{1,6}\\s+${heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`,
    "i",
  );
  let inSection = false;
  const body: string[] = [];
  for (const line of lines) {
    if (!inSection) {
      if (headRe.test(line.trim())) inSection = true;
      continue;
    }
    if (/^#{1,6}\s+/.test(line)) break;
    body.push(line);
  }
  return body.join("\n");
}

/** A section counts as "filled" once it has real content (not just scaffolding). */
function isSectionFilled(md: string, heading: string): boolean {
  const body = sectionBody(md, heading);
  for (const raw of body.split("\n")) {
    const line = raw.replace(/<!--.*?-->/g, "").trim();
    if (!line) continue;
    if (/^_.*_$/.test(line)) continue; // italic placeholder
    if (/^[-*]\s*$/.test(line)) continue; // empty bullet
    if (/^\|[\s|:-]+\|$/.test(line)) continue; // table separator row
    if (/_\.\.\._/.test(line)) continue; // table placeholder row
    return true;
  }
  return false;
}

/** Client-side mirror of the backend Memory-candidates extractor (for counts). */
function parseMemoryCandidates(md: string): string[] {
  const body = sectionBody(md, "Memory candidates");
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of body.split("\n")) {
    const m = raw.match(/^\s*[-*]\s+(.*)$/);
    if (!m) continue;
    const text = m[1].replace(/\s*<!--.*?-->\s*$/, "").trim();
    if (!text) continue;
    if (/^<!--.*-->$/.test(text)) continue;
    if (/^_.*_$/.test(text)) continue;
    if (["...", "…", "-", "—"].includes(text)) continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(text);
  }
  return out;
}

function fmtTimestamp(iso: string | null): string {
  if (!iso) return "Never edited";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderMarkdownPreview(md: string): React.ReactNode {
  // Minimal preview: H2 headings + paragraphs + bullet lists.
  // The full Markdown surface lives elsewhere — this is just enough to
  // verify the section structure before saving.
  const lines = md.split("\n");
  const nodes: React.ReactNode[] = [];
  let listBuffer: string[] = [];
  let paraBuffer: string[] = [];

  const flushList = () => {
    if (listBuffer.length === 0) return;
    nodes.push(
      <ul
        key={`ul-${nodes.length}`}
        className="my-2 list-disc space-y-1 pl-5 text-sm text-fg-secondary"
      >
        {listBuffer.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>,
    );
    listBuffer = [];
  };
  const flushPara = () => {
    if (paraBuffer.length === 0) return;
    nodes.push(
      <p
        key={`p-${nodes.length}`}
        className="my-2 whitespace-pre-wrap text-sm text-fg-secondary"
      >
        {paraBuffer.join("\n")}
      </p>,
    );
    paraBuffer = [];
  };

  for (const line of lines) {
    const h2 = line.match(/^##\s+(.+)$/);
    if (h2) {
      flushList();
      flushPara();
      nodes.push(
        <h3
          key={`h-${nodes.length}`}
          className="mt-4 text-sm font-semibold text-fg-primary"
        >
          {h2[1]}
        </h3>,
      );
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushPara();
      listBuffer.push(bullet[1]);
      continue;
    }
    if (line.trim() === "") {
      flushList();
      flushPara();
      continue;
    }
    flushList();
    paraBuffer.push(line);
  }
  flushList();
  flushPara();

  return nodes;
}

export default function IncidentPostmortemPage() {
  return (
    <Suspense fallback={<DetailSkeleton />}>
      <IncidentPostmortemContent />
    </Suspense>
  );
}

function IncidentPostmortemContent() {
  const navigateDashboard = useDashboardNavigation();
  const searchParams = useSearchParams();
  const id = searchParams.get("id") ?? "";
  const toast = useToast();
  const { user } = useAuth();
  const canEdit = user?.role === "admin" || user?.role === "operator";

  const [incident, setIncident] = useState<IncidentResponse | null>(null);
  const [postmortem, setPostmortem] =
    useState<IncidentPostmortemResponse | null>(null);
  const [draft, setDraft] = useState<string>("");
  const [mode, setMode] = useState<"edit" | "preview">("edit");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    setError("");
    try {
      const [inc, pm] = await Promise.all([
        getIncident(id),
        getIncidentPostmortem(id),
      ]);
      setIncident(inc);
      setPostmortem(pm);
      setDraft(pm.postmortem_md ?? pm.template);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load postmortem");
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void load().finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const dirty = useMemo(() => {
    const stored = postmortem?.postmortem_md ?? "";
    return draft.trim() !== stored.trim();
  }, [draft, postmortem]);

  const candidateCount = useMemo(
    () => parseMemoryCandidates(draft).length,
    [draft],
  );

  const [savingCandidates, setSavingCandidates] = useState(false);

  async function handleSaveCandidates() {
    if (!id) return;
    setSavingCandidates(true);
    setError("");
    try {
      // The endpoint reads the *saved* postmortem, so persist any edits first.
      if (dirty) {
        const updated = await putIncidentPostmortem(id, { postmortem_md: draft });
        setPostmortem(updated);
        setDraft(updated.postmortem_md ?? "");
      }
      const result = await extractPostmortemMemoryCandidates(id);
      if (result.created === 0 && result.skipped === 0) {
        toast.info("No memory candidates found in the postmortem.");
      } else {
        const parts = [`${result.created} memory candidate(s) sent for review`];
        if (result.skipped > 0) parts.push(`${result.skipped} already existed`);
        toast.success(parts.join(" · "), {
          label: "Review",
          href: "/dashboard/memories",
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save candidates");
    } finally {
      setSavingCandidates(false);
    }
  }

  async function handleSave() {
    if (!id) return;
    setSaving(true);
    setError("");
    try {
      const updated = await putIncidentPostmortem(id, {
        postmortem_md: draft,
      });
      setPostmortem(updated);
      setDraft(updated.postmortem_md ?? "");
      toast.success("Postmortem saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    if (!id) return;
    if (
      !confirm(
        "Clear the saved postmortem? The editor will reset to the section template.",
      )
    ) {
      return;
    }
    setSaving(true);
    setError("");
    try {
      const cleared = await putIncidentPostmortem(id, { postmortem_md: "" });
      setPostmortem(cleared);
      setDraft(cleared.template);
      toast.success("Postmortem cleared");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Clear failed");
    } finally {
      setSaving(false);
    }
  }

  function handleResetTemplate() {
    if (!postmortem) return;
    if (
      draft.trim() &&
      !confirm(
        "Replace the current draft with the empty section template? Unsaved edits will be lost.",
      )
    ) {
      return;
    }
    setDraft(postmortem.template);
  }

  function handleGenerateDraft() {
    if (!incident) return;
    if (
      draft.trim() &&
      !confirm(
        "Replace the current draft with a generated draft from this incident's data? Unsaved edits will be lost.",
      )
    ) {
      return;
    }
    setDraft(buildDraftFromIncident(incident));
    setMode("edit");
  }

  const [draftingFromSessions, setDraftingFromSessions] = useState(false);

  async function handleDraftFromSessions() {
    if (!id) return;
    if (
      draft.trim() &&
      !confirm(
        "Replace the current draft with one assembled from this incident's AI sessions (observations, diagnosis, plan)? Unsaved edits will be lost.",
      )
    ) {
      return;
    }
    setDraftingFromSessions(true);
    setError("");
    try {
      const result = await draftIncidentPostmortemFromSessions(id);
      setDraft(result.draft);
      setMode("edit");
      if (result.source_session_ids.length === 0) {
        toast.info("No AI sessions on this incident yet — drafted from lifecycle only.");
      } else {
        toast.success(
          `Drafted from ${result.source_session_ids.length} AI session(s).`,
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not draft from sessions");
    } finally {
      setDraftingFromSessions(false);
    }
  }

  function handleDownload() {
    const filename = `postmortem-${incident?.id?.slice(0, 8) ?? "incident"}.md`;
    const blob = new Blob([draft], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  if (loading) return <DetailSkeleton />;
  if (!id) {
    return (
      <p className="text-sm text-status-critical">Missing incident id.</p>
    );
  }
  if (!incident) {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        <p className="text-sm text-status-critical">
          {error || "Incident not found."}
        </p>
        <Link
          href="/dashboard/incidents"
          className="inline-flex items-center gap-1 text-sm text-accent-text hover:underline"
        >
          <ArrowLeft size={14} /> Back to incidents
        </Link>
      </div>
    );
  }

  const hasStored = (postmortem?.postmortem_md ?? null) !== null;

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <button
          onClick={() => navigateDashboard(`/dashboard/incidents/detail?id=${id}`)}
          className="mb-2 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg-primary"
        >
          <ArrowLeft size={12} /> Back to incident
        </button>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="flex items-center gap-2 text-xl font-bold text-fg-primary sm:text-2xl">
              <ScrollText size={20} className="text-fg-secondary" />
              Postmortem
            </h1>
            <p className="mt-1 truncate text-sm text-fg-secondary">
              {incident.title}
            </p>
            <p className="mt-1 text-xs text-fg-muted">
              Last edited: {fmtTimestamp(postmortem?.postmortem_updated_at ?? null)}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <div className="inline-flex rounded-md border border-border-strong bg-bg-panel p-0.5">
              <button
                onClick={() => setMode("edit")}
                className={`inline-flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  mode === "edit"
                    ? "bg-bg-hover text-fg-primary"
                    : "text-fg-secondary hover:text-fg-primary"
                }`}
              >
                <Pencil size={12} /> Edit
              </button>
              <button
                onClick={() => setMode("preview")}
                className={`inline-flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  mode === "preview"
                    ? "bg-bg-hover text-fg-primary"
                    : "text-fg-secondary hover:text-fg-primary"
                }`}
              >
                <Eye size={12} /> Preview
              </button>
            </div>
            {canEdit && (
              <Button
                size="sm"
                variant="secondary"
                onClick={handleGenerateDraft}
                title="Generate a structured draft from incident details and timeline. You can edit it before saving or downloading."
              >
                <Sparkles size={14} /> Generate draft
              </Button>
            )}
            {canEdit && (
              <Button
                size="sm"
                variant="secondary"
                onClick={handleDraftFromSessions}
                loading={draftingFromSessions}
                title="Assemble a draft from this incident's AI sessions — observations, diagnosis, and proposed actions — mapped onto the postmortem sections."
              >
                <Bot size={14} /> Draft from sessions
              </Button>
            )}
            {canEdit && candidateCount > 0 && (
              <Button
                size="sm"
                variant="secondary"
                onClick={handleSaveCandidates}
                loading={savingCandidates}
                title="Save the Memory candidates bullets as pending memories for review. Approved memories are recalled by the AI in future incidents."
              >
                <Brain size={14} /> Save {candidateCount} to memory
              </Button>
            )}
            {canEdit && (
              <Button
                size="sm"
                onClick={handleSave}
                loading={saving}
                disabled={!dirty}
              >
                <Save size={14} /> Save
              </Button>
            )}
            {draft.trim() && (
              <Button
                size="sm"
                variant="secondary"
                onClick={handleDownload}
                title="Download postmortem as Markdown"
              >
                <Download size={14} /> Download .md
              </Button>
            )}
            {canEdit && hasStored && (
              <Button
                size="sm"
                variant="secondary"
                onClick={handleClear}
                disabled={saving}
              >
                <Trash2 size={14} /> Clear
              </Button>
            )}
          </div>
        </div>
      </div>

      {error && (
        <p className="rounded-md border border-status-critical-border bg-status-critical-bg px-3 py-2 text-xs text-status-critical">
          {error}
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-[1fr_240px]">
        {mode === "edit" ? (
          <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={!canEdit}
              className="block h-[60vh] w-full resize-y rounded-xl bg-transparent px-4 py-4 font-mono text-xs leading-relaxed text-fg-primary focus:outline-none disabled:cursor-not-allowed disabled:opacity-70"
              placeholder="Author the postmortem in Markdown."
              spellCheck
            />
          </div>
        ) : (
          <article className="min-h-[60vh] rounded-xl border border-border-subtle bg-bg-panel px-5 py-4 shadow-sm">
            {draft.trim() ? (
              renderMarkdownPreview(draft)
            ) : (
              <p className="text-sm text-fg-muted">
                Nothing to preview yet. Switch to Edit mode to author the
                postmortem.
              </p>
            )}
          </article>
        )}

        <aside className="space-y-3">
          <div className="rounded-xl border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm">
            {(() => {
              const filledCount = SECTIONS.filter((s) =>
                isSectionFilled(draft, s.heading),
              ).length;
              return (
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-fg-secondary">
                  Recommended sections ({filledCount}/{SECTIONS.length})
                </p>
              );
            })()}
            <ul className="space-y-2">
              {SECTIONS.map((s) => {
                const filled = isSectionFilled(draft, s.heading);
                return (
                  <li key={s.heading} className="flex items-start gap-1.5">
                    {filled ? (
                      <Check
                        size={13}
                        className="mt-0.5 shrink-0 text-status-low"
                        aria-label="filled"
                      />
                    ) : (
                      <Circle
                        size={13}
                        className="mt-0.5 shrink-0 text-fg-muted"
                        aria-label="empty"
                      />
                    )}
                    <div>
                      <p className="text-xs font-medium text-fg-primary">
                        ## {s.heading}
                      </p>
                      <p className="text-[11px] text-fg-muted">{s.hint}</p>
                    </div>
                  </li>
                );
              })}
            </ul>
            {canEdit && (
              <button
                onClick={handleResetTemplate}
                className="mt-3 text-[11px] text-accent-text hover:underline"
              >
                Reset to template
              </button>
            )}
          </div>
          <div className="rounded-xl border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm">
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-fg-secondary">
              Tip
            </p>
            <p className="text-[11px] text-fg-muted">
              Add one bullet per durable lesson under{" "}
              <span className="font-medium">## Memory candidates</span>, then click{" "}
              <span className="font-medium">Save to memory</span>. Each becomes a
              pending memory an admin/operator approves before the AI recalls it.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
