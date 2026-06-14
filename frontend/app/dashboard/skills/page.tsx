"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Copy,
  Download,
  FileText,
  FileUp,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  Wand2,
} from "lucide-react";
import {
  aiSuggestSkill,
  cloneSkill,
  createSkill,
  deleteSkill,
  discoverSkillTools,
  generateSkill,
  getSkillTemplate,
  importSkill,
  listMCPServers,
  listSkills,
  updateSkill,
} from "@/lib/api";
import type {
  MCPServerResponse,
  SkillAssignment,
  SkillClassification,
  SkillDiscoveredTool,
  SkillResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { FormError, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { CardSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

const GLOBAL_SERVER = "__global";
const UNASSIGNED_FILTER = "__unassigned";

const TEMPLATE_SKILL = `---
version: "1"
environment: example
operations:
  - tool: get_pods
    classification: safe
  - tool: scale_deployment
    classification: caution
  - tool: delete_*
    classification: destructive
---

# Example skill

Describe the environment and classification policy here.
`;

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type FormState = {
  name: string;
  description: string;
  // "unassigned" | "global" | <mcp server id>
  assignment: string;
  content: string;
};

// Map the form's assignment value to the API's {assignment, mcp_server_id}.
function resolveAssignment(value: string): {
  assignment: SkillAssignment;
  mcp_server_id: string | null;
} {
  if (value === "unassigned") return { assignment: "unassigned", mcp_server_id: null };
  if (value === "global") return { assignment: "global", mcp_server_id: null };
  return { assignment: "server", mcp_server_id: value };
}

function assignmentFormValue(skill: SkillResponse | null): string {
  if (!skill) return "unassigned"; // new skills start as drafts
  if (skill.assignment === "server" && skill.mcp_server_id) return skill.mcp_server_id;
  if (skill.assignment === "global") return "global";
  return "unassigned";
}

function toFormState(skill: SkillResponse | null, content?: string): FormState {
  return {
    name: skill?.name ?? "",
    description: skill?.description ?? "",
    assignment: assignmentFormValue(skill),
    content: content ?? skill?.content_md ?? TEMPLATE_SKILL,
  };
}

function SkillModal({
  open,
  skill,
  servers,
  initialContent,
  onClose,
  onSaved,
}: {
  open: boolean;
  skill: SkillResponse | null;
  servers: MCPServerResponse[];
  /** Prefill content (e.g. from "New from Template") when creating a new skill. */
  initialContent?: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState<FormState>(toFormState(skill, initialContent));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setForm(toFormState(skill, initialContent));
      setError("");
    }
  }, [open, skill, initialContent]);

  if (!open) return null;

  async function handleSubmit() {
    if (!form.name.trim()) {
      setError("Name is required");
      return;
    }
    if (!form.content.trim()) {
      setError("Skill content is required");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const { assignment, mcp_server_id } = resolveAssignment(form.assignment);
      const payload = {
        name: form.name.trim(),
        content_md: form.content,
        description: form.description.trim() || null,
        mcp_server_id,
        assignment,
      };
      if (skill) {
        await updateSkill(skill.id, payload);
      } else {
        await createSkill(payload);
      }
      await onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={skill ? "Edit skill" : "Add skill"}
      maxWidth="max-w-3xl"
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor="skill-name">Name</Label>
            <Input
              id="skill-name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="production"
            />
          </div>
          <div>
            <Label htmlFor="skill-mcp">Assignment</Label>
            <Select
              id="skill-mcp"
              value={form.assignment}
              onChange={(e) =>
                setForm({ ...form, assignment: e.target.value })
              }
            >
              <option value="unassigned">Unassigned (draft — not used by sessions)</option>
              <option value="global">Global (fallback for all servers)</option>
              {servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
            <p className="mt-1 text-xs text-fg-muted">
              Unassigned skills are saved drafts: editable and downloadable, but
              never injected into AI sessions. A specific server takes
              precedence over the Global fallback.
            </p>
          </div>
        </div>

        <div>
          <Label htmlFor="skill-description">Description</Label>
          <Input
            id="skill-description"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder="Short summary of this skill"
          />
        </div>

        <div>
          <Label htmlFor="skill-content">Skill content (SKILL.md)</Label>
          <Textarea
            id="skill-content"
            value={form.content}
            onChange={(e) => setForm({ ...form, content: e.target.value })}
            rows={18}
            className="font-mono text-xs"
          />
          <p className="mt-1 text-xs text-fg-secondary">
            YAML front-matter between <code>---</code> fences defines
            operations. Use exact MCP tool/action identifiers where possible.
            Content is validated before saving.
          </p>
          <p className="mt-2 rounded-md border border-status-critical-border bg-status-critical-bg px-3 py-2 text-xs text-status-critical">
            Skills guide the AI; the backend tier gate enforces what actually
            runs. Generic command tools (shell, bash, kubectl, aws_cli, gcloud,
            az, terraform, sql, run_command, …) are high-risk and are denied by
            default — blocked at Tier 0/2 and approval-required at Tier 1 — unless
            you explicitly allow narrow command patterns. Deny entries always win.
          </p>
        </div>

        <FormError message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            {skill ? "Save changes" : "Create skill"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function CloneModal({
  open,
  skill,
  servers,
  onClose,
  onSaved,
}: {
  open: boolean;
  skill: SkillResponse | null;
  servers: MCPServerResponse[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [mcpServerId, setMcpServerId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open && skill) {
      setName(`${skill.name}-copy`);
      setMcpServerId(skill.mcp_server_id ?? "");
      setError("");
    }
  }, [open, skill]);

  if (!open || !skill) return null;

  async function handleSubmit() {
    if (!skill) return;
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await cloneSkill(skill.id, {
        name: name.trim(),
        mcp_server_id: mcpServerId || null,
      });
      await onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Clone failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={`Clone skill: ${skill.name}`}>
      <div className="space-y-4">
        <div>
          <Label htmlFor="clone-name">New skill name</Label>
          <Input
            id="clone-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="clone-mcp">Target MCP server</Label>
          <Select
            id="clone-mcp"
            value={mcpServerId}
            onChange={(e) => setMcpServerId(e.target.value)}
          >
            <option value="">Global (fallback for all servers)</option>
            {servers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </div>
        <FormError message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            Clone
          </Button>
        </div>
      </div>
    </Modal>
  );
}

type EditableTool = {
  name: string;
  description: string | null;
  classification: SkillClassification;
  deny: boolean;
  allow_generic: boolean;
  generic: boolean;
  needs_review: boolean;
  rationale: string;
  notes: string;
  // Tier 0 (autonomous) intent + the safety metadata the backend floor needs.
  tier0: boolean;
  reversible: boolean;
  compensating_inverse: string;
};

function fromDiscovered(t: SkillDiscoveredTool): EditableTool {
  return {
    name: t.name,
    description: t.description,
    classification: t.suggested_classification,
    deny: t.suggested_deny,
    allow_generic: false,
    generic: t.generic,
    needs_review: t.needs_review,
    rationale: t.rationale,
    notes: "",
    tier0: false,
    reversible: true,
    compensating_inverse: "",
  };
}

// A non-safe Tier 0 tool only clears the backend safety floor with reversible
// = true AND a compensating inverse. Returns the names that are short of it.
function tier0Incomplete(tools: EditableTool[]): string[] {
  return tools
    .filter(
      (t) =>
        t.tier0 &&
        !t.deny &&
        t.classification !== "safe" &&
        (!t.reversible || !t.compensating_inverse.trim()),
    )
    .map((t) => t.name);
}

/**
 * MCP Skill Studio generator: discover a server's tools, review OpsMender's
 * heuristic classification suggestions, then generate an editable skill draft.
 * The draft is handed to the editor for review/edit before saving — the backend
 * tier gate, not this UI, remains the execution authority.
 */
function GenerateModal({
  open,
  servers,
  onClose,
  onGenerated,
}: {
  open: boolean;
  servers: MCPServerResponse[];
  onClose: () => void;
  onGenerated: (name: string, contentMd: string) => void;
}) {
  const [mcpServerId, setMcpServerId] = useState("");
  const [discovering, setDiscovering] = useState(false);
  const [discovered, setDiscovered] = useState(false);
  const [tools, setTools] = useState<EditableTool[]>([]);
  const [rawTools, setRawTools] = useState<SkillDiscoveredTool[]>([]);
  const [filter, setFilter] = useState("");
  const [name, setName] = useState("New MCP Skill (generated)");
  const [environment, setEnvironment] = useState("production");
  const [intent, setIntent] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [t0, setT0] = useState("");
  const [t1, setT1] = useState("");
  const [t2, setT2] = useState("");
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();

  useEffect(() => {
    if (open) {
      setMcpServerId("");
      setDiscovering(false);
      setDiscovered(false);
      setTools([]);
      setRawTools([]);
      setFilter("");
      setName("New MCP Skill (generated)");
      setEnvironment("production");
      setIntent("");
      setAiBusy(false);
      setT0("");
      setT1("");
      setT2("");
      setError("");
    }
  }, [open]);

  if (!open) return null;

  async function handleDiscover() {
    if (!mcpServerId) {
      setError("Select an MCP server to discover its tools.");
      return;
    }
    setDiscovering(true);
    setError("");
    try {
      const res = await discoverSkillTools(mcpServerId);
      setRawTools(res.tools);
      setTools(res.tools.map(fromDiscovered));
      setFilter("");
      setDiscovered(true);
      const serverName = servers.find((s) => s.id === mcpServerId)?.name;
      if (serverName) setName(`${serverName} skill`);
      if (res.tools.length === 0) {
        toast.info("The MCP server exposed no tools.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Tool discovery failed");
    } finally {
      setDiscovering(false);
    }
  }

  function patchTool(index: number, patch: Partial<EditableTool>) {
    setTools((current) =>
      current.map((t, i) => (i === index ? { ...t, ...patch } : t)),
    );
  }

  function denyAllGeneric() {
    setTools((current) =>
      current.map((t) => (t.generic ? { ...t, deny: true } : t)),
    );
  }

  function resetToSuggestions() {
    setTools(rawTools.map(fromDiscovered));
  }

  async function handleAiAssist() {
    if (tools.length === 0) return;
    setAiBusy(true);
    setError("");
    try {
      const res = await aiSuggestSkill({
        intent,
        environment,
        tools: tools.map((t) => ({ name: t.name, description: t.description })),
      });
      const byName = new Map(res.tools.map((t) => [t.name, t]));
      setTools((current) =>
        current.map((t) => {
          const s = byName.get(t.name);
          if (!s) return t;
          // The model proposes Tier 0 metadata by returning reversible/inverse;
          // map that into the row's Tier 0 intent for non-safe tools.
          const wantsTier0 =
            s.classification !== "safe" &&
            !s.deny &&
            (s.reversible === true || !!s.compensating_inverse);
          return {
            ...t,
            classification: s.classification,
            deny: s.deny,
            allow_generic: s.allow_generic,
            needs_review: s.needs_review,
            rationale: s.rationale || t.rationale,
            tier0: wantsTier0,
            reversible: s.reversible ?? t.reversible,
            compensating_inverse: s.compensating_inverse ?? t.compensating_inverse,
          };
        }),
      );
      if (res.tier0_instructions) setT0(res.tier0_instructions);
      if (res.tier1_instructions) setT1(res.tier1_instructions);
      if (res.tier2_instructions) setT2(res.tier2_instructions);
      if (res.environment) setEnvironment(res.environment);
      toast.success("AI suggestions applied — review the flagged rows.");
    } catch (err) {
      // AI assist is optional: degrade to the heuristic suggestions already shown.
      setError(
        err instanceof Error
          ? `${err.message} You can still classify tools manually.`
          : "AI assist failed; classify tools manually.",
      );
    } finally {
      setAiBusy(false);
    }
  }

  async function handleGenerate() {
    if (tools.length === 0) {
      setError("Discover tools first, or pick a server that exposes tools.");
      return;
    }
    // Block generation when a Tier 0 tool lacks the safety metadata the backend
    // floor requires — otherwise it would silently never run autonomously.
    const incomplete = tier0Incomplete(tools);
    if (incomplete.length > 0) {
      setError(
        `Tier 0 (autonomous) actions need "reversible" and a compensating ` +
          `inverse to pass the backend safety floor. Complete or untick Tier 0 ` +
          `for: ${incomplete.join(", ")}.`,
      );
      return;
    }
    // Soft guard: nudge the operator about still-flagged rows (AI downgrades,
    // unrecognized tools, autonomous-destructive). Not a hard block.
    const flagged = tools.filter((t) => t.needs_review).length;
    if (
      flagged > 0 &&
      !window.confirm(
        `${flagged} tool(s) are still flagged for review. Generate the draft ` +
          `anyway? You can keep editing it before saving.`,
      )
    ) {
      return;
    }
    setGenerating(true);
    setError("");
    try {
      const res = await generateSkill({
        name: name.trim() || "New MCP Skill (generated)",
        environment: environment.trim() || "your-environment",
        operations: tools.map((t) => {
          const tier0NonSafe = t.tier0 && !t.deny && t.classification !== "safe";
          return {
            tool: t.name,
            classification: t.classification,
            deny: t.deny,
            allow_generic: t.allow_generic,
            // Only emit Tier 0 safety metadata when the operator opted the tool
            // into autonomous execution; otherwise leave it unset.
            reversible: tier0NonSafe ? t.reversible : null,
            compensating_inverse: tier0NonSafe
              ? t.compensating_inverse.trim() || null
              : null,
            notes: t.notes.trim() || null,
          };
        }),
        tier0_instructions: t0,
        tier1_instructions: t1,
        tier2_instructions: t2,
      });
      onGenerated(res.name, res.content_md);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Generate skill from MCP server"
      maxWidth="max-w-4xl"
    >
      <div className="space-y-4">
        <p className="text-sm text-fg-secondary">
          Discover an MCP server&apos;s tools, review the suggested
          classifications, then generate a draft you can edit before saving.
          Suggestions are heuristic — the backend tier gate enforces what can
          actually run.
        </p>

        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[16rem] flex-1">
            <Label htmlFor="gen-mcp">MCP server</Label>
            <Select
              id="gen-mcp"
              value={mcpServerId}
              onChange={(e) => setMcpServerId(e.target.value)}
            >
              <option value="">Select a server…</option>
              {servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </div>
          <Button
            variant="secondary"
            onClick={handleDiscover}
            loading={discovering}
            disabled={!mcpServerId}
          >
            <Sparkles size={14} /> Discover tools
          </Button>
        </div>

        {discovered && tools.length > 0 && (
          <>
            <div className="rounded-md border border-border-subtle bg-bg-elevated p-3">
              <Label htmlFor="gen-intent">
                Intent / context for AI assist (optional)
              </Label>
              <Textarea
                id="gen-intent"
                rows={2}
                value={intent}
                onChange={(e) => setIntent(e.target.value)}
                placeholder="e.g. Production Kubernetes. Be conservative — never auto-delete; restarts OK if health checks pass."
              />
              <div className="mt-2 flex items-center justify-between gap-2">
                <p className="text-xs text-fg-secondary">
                  AI suggestions are reviewed by you; generic command tools stay
                  denied and downgrades are flagged. The tier gate enforces what
                  runs.
                </p>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleAiAssist}
                  loading={aiBusy}
                >
                  <Sparkles size={14} /> AI assist
                </Button>
              </div>
            </div>

            <p className="text-xs text-fg-secondary">
              Tier 0 actions require enough rollback/safety metadata to pass
              backend enforcement. Skills guide the AI, but the backend tier gate
              decides what can actually run.
            </p>

            <div className="flex flex-wrap items-center gap-2">
              <Input
                aria-label="Filter tools"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter tools…"
                className="max-w-[16rem]"
              />
              <Button variant="secondary" size="sm" onClick={denyAllGeneric}>
                Deny all generic
              </Button>
              <Button variant="secondary" size="sm" onClick={resetToSuggestions}>
                Reset to suggestions
              </Button>
              <span className="ml-auto text-xs text-fg-secondary">
                {tools.length} tools · {tools.filter((t) => t.needs_review).length}{" "}
                flagged · {tools.filter((t) => t.deny).length} denied
              </span>
            </div>

            <div className="max-h-[22rem] overflow-y-auto rounded-md border border-border-subtle">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-bg-elevated text-left text-xs text-fg-secondary">
                  <tr>
                    <th className="px-3 py-2">Tool</th>
                    <th className="px-3 py-2">Classification</th>
                    <th className="px-3 py-2">Deny</th>
                    <th className="px-3 py-2">Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {tools
                    .map((t, i) => ({ t, i }))
                    .filter(({ t }) => {
                      const q = filter.trim().toLowerCase();
                      if (!q) return true;
                      return `${t.name} ${t.description ?? ""}`
                        .toLowerCase()
                        .includes(q);
                    })
                    .map(({ t, i }) => (
                    <tr key={t.name} className="border-t border-border-subtle align-top">
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap items-center gap-1">
                          <span className="font-mono text-xs text-fg-primary">
                            {t.name}
                          </span>
                          {t.generic && <Badge variant="closed">generic</Badge>}
                          {t.needs_review && (
                            <Badge variant="in_progress">review</Badge>
                          )}
                        </div>
                        {t.description && (
                          <p className="mt-0.5 text-xs text-fg-secondary">
                            {t.description}
                          </p>
                        )}
                        <p className="mt-0.5 text-[11px] text-fg-tertiary">
                          {t.rationale}
                        </p>
                      </td>
                      <td className="px-3 py-2">
                        <Select
                          aria-label={`Classification for ${t.name}`}
                          value={t.classification}
                          onChange={(e) =>
                            patchTool(i, {
                              classification: e.target.value as SkillClassification,
                            })
                          }
                        >
                          <option value="safe">safe</option>
                          <option value="caution">caution</option>
                          <option value="destructive">destructive</option>
                        </Select>
                        {t.generic && !t.deny && (
                          <label className="mt-1 flex items-center gap-1 text-[11px] text-fg-secondary">
                            <input
                              type="checkbox"
                              checked={t.allow_generic}
                              onChange={(e) =>
                                patchTool(i, { allow_generic: e.target.checked })
                              }
                            />
                            allow_generic
                          </label>
                        )}
                        {!t.deny && (
                          <label className="mt-1 flex items-center gap-1 text-[11px] text-fg-secondary">
                            <input
                              type="checkbox"
                              aria-label={`Allow ${t.name} at Tier 0`}
                              checked={t.tier0}
                              onChange={(e) =>
                                patchTool(i, { tier0: e.target.checked })
                              }
                            />
                            Tier 0 (autonomous)
                          </label>
                        )}
                        {t.tier0 && !t.deny && t.classification !== "safe" && (
                          <div className="mt-1 space-y-1 rounded border border-border-subtle bg-bg-base p-1.5">
                            <label className="flex items-center gap-1 text-[11px] text-fg-secondary">
                              <input
                                type="checkbox"
                                aria-label={`Reversible ${t.name}`}
                                checked={t.reversible}
                                onChange={(e) =>
                                  patchTool(i, { reversible: e.target.checked })
                                }
                              />
                              reversible
                            </label>
                            <Input
                              aria-label={`Compensating inverse for ${t.name}`}
                              value={t.compensating_inverse}
                              onChange={(e) =>
                                patchTool(i, {
                                  compensating_inverse: e.target.value,
                                })
                              }
                              placeholder="compensating inverse (rollback tool)"
                            />
                            {(!t.reversible || !t.compensating_inverse.trim()) && (
                              <p className="text-[11px] text-status-medium">
                                Needs reversible + inverse to clear the Tier 0
                                floor; otherwise it runs at Tier 1 approval.
                              </p>
                            )}
                          </div>
                        )}
                        {t.tier0 && !t.deny && t.classification === "safe" && (
                          <p className="mt-1 text-[11px] text-fg-tertiary">
                            Read-only — clears Tier 0 automatically.
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          aria-label={`Deny ${t.name}`}
                          checked={t.deny}
                          onChange={(e) => patchTool(i, { deny: e.target.checked })}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <Input
                          aria-label={`Notes for ${t.name}`}
                          value={t.notes}
                          onChange={(e) => patchTool(i, { notes: e.target.value })}
                          placeholder="optional"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <Label htmlFor="gen-name">Skill name</Label>
                <Input
                  id="gen-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="gen-env">Environment</Label>
                <Input
                  id="gen-env"
                  value={environment}
                  onChange={(e) => setEnvironment(e.target.value)}
                  placeholder="production"
                />
              </div>
            </div>

            <div className="space-y-2">
              <div>
                <Label htmlFor="gen-t0">Tier 0 instructions (optional)</Label>
                <Textarea
                  id="gen-t0"
                  rows={2}
                  value={t0}
                  onChange={(e) => setT0(e.target.value)}
                  placeholder="Freeform guidance for autonomous remediation."
                />
              </div>
              <div>
                <Label htmlFor="gen-t1">Tier 1 instructions (optional)</Label>
                <Textarea
                  id="gen-t1"
                  rows={2}
                  value={t1}
                  onChange={(e) => setT1(e.target.value)}
                  placeholder="Freeform guidance for approval-gated response."
                />
              </div>
              <div>
                <Label htmlFor="gen-t2">Tier 2 instructions (optional)</Label>
                <Textarea
                  id="gen-t2"
                  rows={2}
                  value={t2}
                  onChange={(e) => setT2(e.target.value)}
                  placeholder="Freeform advisory guidance."
                />
              </div>
            </div>
          </>
        )}

        <FormError message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={generating}>
            Cancel
          </Button>
          <Button
            onClick={handleGenerate}
            loading={generating}
            disabled={!discovered || tools.length === 0}
          >
            <Wand2 size={14} /> Generate draft
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ImportModal({
  open,
  servers,
  onClose,
  onSaved,
}: {
  open: boolean;
  servers: MCPServerResponse[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [mcpServerId, setMcpServerId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setFile(null);
      setName("");
      setMcpServerId("");
      setError("");
      if (fileInput.current) fileInput.current.value = "";
    }
  }, [open]);

  if (!open) return null;

  async function handleSubmit() {
    if (!file) {
      setError("Choose a .md file to import");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await importSkill({
        file,
        name: name.trim() || undefined,
        mcp_server_id: mcpServerId || undefined,
      });
      await onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Import skill file">
      <div className="space-y-4">
        <div>
          <Label htmlFor="import-file">SKILL.md file</Label>
          <input
            ref={fileInput}
            id="import-file"
            type="file"
            accept=".md,.markdown,text/markdown"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-fg-primary file:mr-3 file:rounded-md file:border-0 file:bg-accent-bg file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-accent hover:file:bg-accent-bg"
          />
        </div>
        <div>
          <Label htmlFor="import-name">Name (optional)</Label>
          <Input
            id="import-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Defaults to filename"
          />
        </div>
        <div>
          <Label htmlFor="import-mcp">MCP server</Label>
          <Select
            id="import-mcp"
            value={mcpServerId}
            onChange={(e) => setMcpServerId(e.target.value)}
          >
            <option value="">Global (fallback for all servers)</option>
            {servers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </div>
        <FormError message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            Import
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export default function SkillsPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const [skills, setSkills] = useState<SkillResponse[]>([]);
  const [servers, setServers] = useState<MCPServerResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<SkillResponse | null>(null);
  const [showEdit, setShowEdit] = useState(false);
  const [cloning, setCloning] = useState<SkillResponse | null>(null);
  const [showClone, setShowClone] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [showGenerate, setShowGenerate] = useState(false);
  const [templateContent, setTemplateContent] = useState<string | undefined>(undefined);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [skillList, serverList] = await Promise.all([
        listSkills(),
        listMCPServers(),
      ]);
      setSkills(skillList.items);
      setServers(serverList.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load skills");
    }
  }, [toast]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    load().finally(() => setLoading(false));
  }, [load]);

  const serverNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const s of servers) map.set(s.id, s.name);
    return map;
  }, [servers]);

  const serverFilterOptions = useMemo(
    () => [
      { value: UNASSIGNED_FILTER, label: "Unassigned" },
      { value: GLOBAL_SERVER, label: "Global" },
      ...servers.map((server) => ({
        value: server.id,
        label: server.name,
      })),
    ],
    [servers],
  );

  async function handleDelete(skill: SkillResponse) {
    if (!confirm(`Delete skill "${skill.name}"?`)) return;
    try {
      await deleteSkill(skill.id);
      toast.success(`Deleted "${skill.name}"`);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  }

  function openCreate() {
    setEditing(null);
    setTemplateContent(undefined);
    setShowEdit(true);
  }

  async function handleNewFromTemplate() {
    try {
      const tmpl = await getSkillTemplate();
      setEditing(null);
      setTemplateContent(tmpl.content_md);
      setShowEdit(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load template");
    }
  }

  function handleGenerated(_name: string, contentMd: string) {
    // Hand the generated draft to the editor for review/edit before saving.
    setShowGenerate(false);
    setEditing(null);
    setTemplateContent(contentMd);
    setShowEdit(true);
  }

  function handleDownload(skill: SkillResponse) {
    const safe = skill.name.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "skill";
    const blob = new Blob([skill.content_md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safe}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }
  function openEdit(skill: SkillResponse) {
    setEditing(skill);
    setShowEdit(true);
  }
  function openClone(skill: SkillResponse) {
    setCloning(skill);
    setShowClone(true);
  }

  const columns = useMemo<DataTableColumn<SkillResponse>[]>(
    () => [
      {
        id: "name",
        label: "Skill",
        accessor: (skill) => `${skill.name} ${skill.description ?? ""}`,
        cell: (skill) => (
          <div className="min-w-[14rem]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-fg-primary">
                {skill.name}
              </span>
              {skill.assignment === "server" && skill.mcp_server_id ? (
                <Badge variant="in_progress">
                  {serverNameById.get(skill.mcp_server_id) ?? "server"}
                </Badge>
              ) : skill.assignment === "unassigned" ? (
                <Badge variant="closed">unassigned</Badge>
              ) : (
                <Badge variant="default">global</Badge>
              )}
            </div>
            {skill.description && (
              <p className="mt-1 line-clamp-2 max-w-xl text-xs text-fg-secondary">
                {skill.description}
              </p>
            )}
          </div>
        ),
        sortable: true,
        searchable: true,
      },
      {
        id: "mcp_server",
        label: "Assignment",
        accessor: (skill) =>
          skill.assignment === "server" && skill.mcp_server_id
            ? serverNameById.get(skill.mcp_server_id) ?? skill.mcp_server_id
            : skill.assignment === "unassigned"
              ? "Unassigned"
              : "Global",
        cell: (skill) => (
          <span className="text-sm text-fg-secondary">
            {skill.assignment === "server" && skill.mcp_server_id
              ? serverNameById.get(skill.mcp_server_id) ?? skill.mcp_server_id
              : skill.assignment === "unassigned"
                ? "Unassigned (draft)"
                : "Global fallback"}
          </span>
        ),
        sortable: true,
        filterChips: {
          options: serverFilterOptions,
          valueOf: (skill) =>
            skill.assignment === "server" && skill.mcp_server_id
              ? skill.mcp_server_id
              : skill.assignment === "unassigned"
                ? UNASSIGNED_FILTER
                : GLOBAL_SERVER,
        },
      },
      {
        id: "focus_areas",
        label: "Focus areas",
        accessor: (skill) => skill.focus_areas.join(" "),
        cell: (skill) =>
          skill.focus_areas.length > 0 ? (
            <div className="flex max-w-sm flex-wrap gap-1">
              {skill.focus_areas.map((area) => (
                <Badge key={area} variant="default">
                  {area}
                </Badge>
              ))}
            </div>
          ) : (
            <span className="text-fg-muted">—</span>
          ),
        searchable: true,
      },
      {
        id: "updated_at",
        label: "Updated",
        accessor: (skill) => skill.updated_at,
        cell: (skill) => (
          <span className="whitespace-nowrap text-xs text-fg-secondary">
            {fmtDate(skill.updated_at)}
          </span>
        ),
        sortable: true,
      },
      {
        id: "created_at",
        label: "Created",
        accessor: (skill) => skill.created_at,
        cell: (skill) => (
          <span className="whitespace-nowrap text-xs text-fg-secondary">
            {fmtDate(skill.created_at)}
          </span>
        ),
        sortable: true,
        hiddenByDefault: true,
      },
    ],
    [serverFilterOptions, serverNameById],
  );

  if (loading) {
    return (
      <div className="space-y-6">
        <CardSkeleton lines={2} />
        <CardSkeleton lines={3} />
        <CardSkeleton lines={3} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="MCP Skills"
        subtitle="MCP Skill Studio — create, edit, assign, and download skill policies. MCP Skills guide the AI; the backend tier gate enforces what can actually run."
        actions={
          canEdit ? (
            <div className="flex items-center gap-2">
              <Button variant="secondary" onClick={() => setShowImport(true)}>
                <FileUp size={14} /> Import .md
              </Button>
              <Button variant="secondary" onClick={() => setShowGenerate(true)}>
                <Wand2 size={14} /> Generate from MCP
              </Button>
              <Button onClick={handleNewFromTemplate}>
                <Sparkles size={14} /> New from Template
              </Button>
            </div>
          ) : undefined
        }
      />

      {skills.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No skills yet"
          description={
            canEdit
              ? "MCP Skills define how the AI should use tools for each autonomy tier — action order, allow lists, approval-required actions, deny lists, and environment rules. Start from the 3-tier template."
              : "Ask an admin to import or create an MCP Skill."
          }
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/mcp-skills.md"
          learnMoreLabel="MCP Skills guide"
          action={
            canEdit ? (
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="sm" onClick={() => setShowImport(true)}>
                  <FileUp size={14} /> Import .md
                </Button>
                <Button variant="secondary" size="sm" onClick={() => setShowGenerate(true)}>
                  <Wand2 size={14} /> Generate from MCP
                </Button>
                <Button size="sm" onClick={handleNewFromTemplate}>
                  <Sparkles size={14} /> New from Template
                </Button>
              </div>
            ) : undefined
          }
        />
      ) : (
        <DataTable
          rows={skills}
          columns={columns}
          rowKey={(skill) => skill.id}
          storageKey="opsmender:skills-table"
          filterBar
          searchPlaceholder="Search skill, description, focus area, or server…"
          toolbarRight={
            canEdit ? (
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="sm" onClick={() => setShowGenerate(true)}>
                  <Wand2 size={14} /> Generate from MCP
                </Button>
                <Button variant="secondary" size="sm" onClick={handleNewFromTemplate}>
                  <Sparkles size={14} /> New from Template
                </Button>
                <Button size="sm" onClick={openCreate}>
                  <Plus size={14} /> New skill
                </Button>
              </div>
            ) : undefined
          }
          rowActions={(skill) => (
            <div className="flex justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => handleDownload(skill)}
                title="Download as Markdown"
              >
                <Download size={14} /> Download
              </Button>
              {canEdit && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => openEdit(skill)}
                >
                  <Pencil size={14} /> Edit
                </Button>
              )}
              {canEdit && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => openClone(skill)}
                >
                  <Copy size={14} /> Clone
                </Button>
              )}
              {canEdit && (
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => handleDelete(skill)}
                >
                  <Trash2 size={14} />
                </Button>
              )}
            </div>
          )}
        />
      )}

      <SkillModal
        open={showEdit}
        skill={editing}
        servers={servers}
        initialContent={templateContent}
        onClose={() => setShowEdit(false)}
        onSaved={load}
      />
      <CloneModal
        open={showClone}
        skill={cloning}
        servers={servers}
        onClose={() => setShowClone(false)}
        onSaved={load}
      />
      <ImportModal
        open={showImport}
        servers={servers}
        onClose={() => setShowImport(false)}
        onSaved={load}
      />
      <GenerateModal
        open={showGenerate}
        servers={servers}
        onClose={() => setShowGenerate(false)}
        onGenerated={handleGenerated}
      />
    </div>
  );
}
