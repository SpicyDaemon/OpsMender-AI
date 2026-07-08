"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Clipboard, KeyRound, Plus, RefreshCw, Trash2 } from "lucide-react";

import {
  createApiToken,
  listApiTokens,
  revokeApiToken,
} from "@/lib/api";
import type {
  ApiTokenCreateResponse,
  ApiTokenResponse,
  ApiTokenRole,
} from "@/lib/types";
import { formatDateTime } from "@/lib/formatDate";
import { roleLabel } from "@/lib/displayNames";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { FormAlert, Input, Label, Select } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";

const ROLE_OPTIONS: Array<{
  value: ApiTokenRole;
  description: string;
}> = [
  {
    value: "operator",
    description: "Can read operations data and use operator endpoints.",
  },
  {
    value: "viewer",
    description: "Can read viewer-accessible data only.",
  },
  {
    value: "admin",
    description: "Can use administrator endpoints outside denylisted self-service.",
  },
];

function tokenStatus(row: ApiTokenResponse) {
  return row.revoked_at ? "Revoked" : "Active";
}

export function ApiTokensSection() {
  const [tokens, setTokens] = useState<ApiTokenResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState<ApiTokenRole>("operator");
  const [created, setCreated] = useState<ApiTokenCreateResponse | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ApiTokenResponse | null>(null);
  const [revoking, setRevoking] = useState(false);
  const toast = useToast();

  const loadTokens = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listApiTokens();
      setTokens(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "API tokens failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTokens();
  }, [loadTokens]);

  const resetCreate = () => {
    setName("");
    setRole("operator");
    setCreated(null);
    setCreateOpen(false);
  };

  const submitCreate = async () => {
    if (!name.trim()) {
      setError("Token name is required");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const token = await createApiToken({ name: name.trim(), role });
      setCreated(token);
      setName("");
      await loadTokens();
    } catch (err) {
      setError(err instanceof Error ? err.message : "API token could not be created");
    } finally {
      setCreating(false);
    }
  };

  const copySecret = async () => {
    if (!created) return;
    try {
      await window.navigator.clipboard.writeText(created.token);
      toast.success("API token copied");
    } catch {
      toast.error("Copy failed");
    }
  };

  const confirmRevoke = async () => {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await revokeApiToken(revokeTarget.id);
      setRevokeTarget(null);
      await loadTokens();
      toast.success("API token revoked");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Revoke failed");
    } finally {
      setRevoking(false);
    }
  };

  const columns = useMemo<DataTableColumn<ApiTokenResponse>[]>(
    () => [
      {
        id: "name",
        label: "Name",
        accessor: (row) => row.name,
        cell: (row) => (
          <div className="min-w-0">
            <div className="font-medium text-fg-primary">{row.name}</div>
            <div className="mt-0.5 font-mono text-[11px] text-fg-muted">
              {row.token_prefix}…
            </div>
          </div>
        ),
        sortable: true,
        searchable: true,
      },
      {
        id: "role",
        label: "Role",
        accessor: (row) => roleLabel(row.role),
        cell: (row) => <Badge variant="default">{roleLabel(row.role)}</Badge>,
        sortable: true,
      },
      {
        id: "created",
        label: "Created",
        accessor: (row) => row.created_at,
        cell: (row) => formatDateTime(row.created_at),
        sortable: true,
      },
      {
        id: "last_used",
        label: "Last Used",
        accessor: (row) => row.last_used_at ?? "",
        cell: (row) => row.last_used_at ? formatDateTime(row.last_used_at) : "Never",
        sortable: true,
      },
      {
        id: "status",
        label: "Status",
        accessor: tokenStatus,
        cell: (row) => (
          <Badge variant={row.revoked_at ? "default" : "low"}>
            {tokenStatus(row)}
          </Badge>
        ),
        sortable: true,
      },
    ],
    [],
  );

  return (
    <section className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-primary">
            API Tokens
          </h2>
          <p className="mt-1 text-sm text-fg-secondary">
            Named bearer tokens for scripts and automation.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={loadTokens} loading={loading}>
            <RefreshCw size={14} aria-hidden="true" /> Refresh
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus size={14} aria-hidden="true" /> Create token
          </Button>
        </div>
      </div>

      {error ? <FormAlert message={error} /> : null}

      {tokens.length ? (
        <DataTable<ApiTokenResponse>
          rows={tokens}
          columns={columns}
          rowKey={(row) => row.id}
          hideToolbar
          rowActions={(row) =>
            row.revoked_at ? null : (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setRevokeTarget(row)}
                aria-label={`Revoke ${row.name}`}
                title={`Revoke ${row.name}`}
              >
                <Trash2 size={14} aria-hidden="true" />
              </Button>
            )
          }
          empty={
            <EmptyState
              icon={KeyRound}
              title="No API tokens"
              description="Create a token when an automation job needs REST API access."
            />
          }
        />
      ) : loading ? (
        <div className="h-28 animate-pulse rounded-lg border border-border-subtle bg-bg-panel" />
      ) : (
        <EmptyState
          icon={KeyRound}
          title="No API tokens"
          description="Create a token when an automation job needs REST API access."
        />
      )}

      <Modal open={createOpen} onClose={resetCreate} title="Create API Token">
        {created ? (
          <div className="space-y-4">
            <FormAlert
              tone="info"
              message="Copy this token now. It will not be shown again."
            />
            <div>
              <Label htmlFor="api-token-secret">Token</Label>
              <div className="flex gap-2">
                <Input
                  id="api-token-secret"
                  value={created.token}
                  readOnly
                  className="font-mono text-xs"
                />
                <Button variant="secondary" onClick={copySecret}>
                  <Clipboard size={14} aria-hidden="true" /> Copy
                </Button>
              </div>
            </div>
            <div className="flex justify-end">
              <Button onClick={resetCreate}>Done</Button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <Label htmlFor="api-token-name" required>
                Name
              </Label>
              <Input
                id="api-token-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={150}
                placeholder="deploy-script"
              />
            </div>
            <div>
              <Label htmlFor="api-token-role">Role</Label>
              <Select
                id="api-token-role"
                value={role}
                onChange={(event) => setRole(event.target.value as ApiTokenRole)}
              >
                {ROLE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {roleLabel(option.value)}
                  </option>
                ))}
              </Select>
              <div className="mt-2 space-y-1 text-xs text-fg-muted">
                {ROLE_OPTIONS.map((option) => (
                  <p key={option.value}>
                    <span className="font-medium text-fg-secondary">
                      {roleLabel(option.value)}:
                    </span>{" "}
                    {option.description}
                  </p>
                ))}
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={resetCreate}>
                Cancel
              </Button>
              <Button onClick={submitCreate} loading={creating}>
                Create token
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={Boolean(revokeTarget)}
        onClose={() => setRevokeTarget(null)}
        title="Revoke API Token"
      >
        <div className="space-y-4">
          <p className="text-sm text-fg-secondary">
            Revoke {revokeTarget ? revokeTarget.name : "this token"} immediately?
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setRevokeTarget(null)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmRevoke} loading={revoking}>
              Revoke
            </Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}
