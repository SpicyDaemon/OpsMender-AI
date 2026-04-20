"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Copy,
  FileText,
  FileUp,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import {
  cloneSkill,
  createSkill,
  deleteSkill,
  importSkill,
  listMCPServers,
  listSkills,
  updateSkill,
} from "@/lib/api";
import type {
  MCPServerResponse,
  SkillResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { FormError, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { CardSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

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
  mcpServerId: string;
  content: string;
};

function toFormState(skill: SkillResponse | null): FormState {
  return {
    name: skill?.name ?? "",
    description: skill?.description ?? "",
    mcpServerId: skill?.mcp_server_id ?? "",
    content: skill?.content_md ?? TEMPLATE_SKILL,
  };
}

function SkillModal({
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
  const [form, setForm] = useState<FormState>(toFormState(skill));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setForm(toFormState(skill));
      setError("");
    }
  }, [open, skill]);

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
      const payload = {
        name: form.name.trim(),
        content_md: form.content,
        description: form.description.trim() || null,
        mcp_server_id: form.mcpServerId || null,
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
            <Label htmlFor="skill-mcp">MCP server</Label>
            <Select
              id="skill-mcp"
              value={form.mcpServerId}
              onChange={(e) =>
                setForm({ ...form, mcpServerId: e.target.value })
              }
            >
              <option value="">Global (fallback for all servers)</option>
              {servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
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
            operations. Content is validated before saving.
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
    setLoading(true);
    load().finally(() => setLoading(false));
  }, [load]);

  const serverNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const s of servers) map.set(s.id, s.name);
    return map;
  }, [servers]);

  const grouped = useMemo(() => {
    const byServer = new Map<string, SkillResponse[]>();
    const globals: SkillResponse[] = [];
    for (const skill of skills) {
      if (!skill.mcp_server_id) {
        globals.push(skill);
        continue;
      }
      const bucket = byServer.get(skill.mcp_server_id);
      if (bucket) bucket.push(skill);
      else byServer.set(skill.mcp_server_id, [skill]);
    }
    return { byServer, globals };
  }, [skills]);

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
    setShowEdit(true);
  }
  function openEdit(skill: SkillResponse) {
    setEditing(skill);
    setShowEdit(true);
  }
  function openClone(skill: SkillResponse) {
    setCloning(skill);
    setShowClone(true);
  }

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
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-fg-primary">Skills</h1>
          <p className="mt-1 text-sm text-fg-secondary">
            Operator-owned skill definitions. Each MCP server can have its
            own skill; a global skill acts as the fallback.
          </p>
        </div>
        {canEdit && (
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={() => setShowImport(true)}
            >
              <FileUp size={14} /> Import .md
            </Button>
            <Button onClick={openCreate}>
              <Plus size={14} /> New skill
            </Button>
          </div>
        )}
      </div>

      {skills.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No skills yet"
          description={
            canEdit
              ? "Skills classify MCP tool calls as safe, caution, or destructive. Import a SKILL.md file or author one from scratch."
              : "Ask an admin to import or create a SKILL.md file."
          }
          action={
            canEdit ? (
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="sm" onClick={() => setShowImport(true)}>
                  <FileUp size={14} /> Import .md
                </Button>
                <Button size="sm" onClick={openCreate}>
                  <Plus size={14} /> New skill
                </Button>
              </div>
            ) : undefined
          }
        />
      ) : (
        <div className="space-y-6">
          <SkillGroup
            title="Global (fallback)"
            subtitle="Used when the active MCP server has no bound skill"
            skills={grouped.globals}
            serverNameById={serverNameById}
            canEdit={canEdit}
            onEdit={openEdit}
            onClone={openClone}
            onDelete={handleDelete}
          />
          {servers.map((server) => {
            const bucket = grouped.byServer.get(server.id) ?? [];
            if (bucket.length === 0) return null;
            return (
              <SkillGroup
                key={server.id}
                title={server.name}
                subtitle={`MCP server — ${server.transport}`}
                skills={bucket}
                serverNameById={serverNameById}
                canEdit={canEdit}
                onEdit={openEdit}
                onClone={openClone}
                onDelete={handleDelete}
              />
            );
          })}
        </div>
      )}

      <SkillModal
        open={showEdit}
        skill={editing}
        servers={servers}
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
    </div>
  );
}

function SkillGroup({
  title,
  subtitle,
  skills,
  serverNameById,
  canEdit,
  onEdit,
  onClone,
  onDelete,
}: {
  title: string;
  subtitle?: string;
  skills: SkillResponse[];
  serverNameById: Map<string, string>;
  canEdit: boolean;
  onEdit: (s: SkillResponse) => void;
  onClone: (s: SkillResponse) => void;
  onDelete: (s: SkillResponse) => void;
}) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
      <div className="border-b border-border-subtle px-6 py-4">
        <h2 className="text-base font-semibold text-fg-primary">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-fg-secondary">{subtitle}</p>}
      </div>
      <div className="divide-y divide-border-subtle">
        {skills.length === 0 ? (
          <p className="px-6 py-4 text-sm text-fg-secondary">No skills.</p>
        ) : (
          skills.map((skill) => (
            <div
              key={skill.id}
              className="flex items-start justify-between gap-4 px-6 py-4"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-fg-primary">{skill.name}</span>
                  {skill.mcp_server_id ? (
                    <Badge variant="in_progress">
                      {serverNameById.get(skill.mcp_server_id) ?? "server"}
                    </Badge>
                  ) : (
                    <Badge variant="default">global</Badge>
                  )}
                </div>
                {skill.description && (
                  <p className="mt-1 text-sm text-fg-secondary">
                    {skill.description}
                  </p>
                )}
                <p className="mt-1 text-xs text-fg-muted">
                  Updated {fmtDate(skill.updated_at)}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                {canEdit && (
                  <>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => onEdit(skill)}
                    >
                      <Pencil size={14} /> Edit
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => onClone(skill)}
                    >
                      <Copy size={14} /> Clone
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => onDelete(skill)}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
