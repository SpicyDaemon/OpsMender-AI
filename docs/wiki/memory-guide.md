# AI Incident Memory

> **Status (2026-05-23):** Sprint 45 near-complete. Agent-side surface (`recall` / `remember` / auto-compaction) live as of Session 115. REST API (`GET/POST/PUT/DELETE /memories` + `/feedback` + `/hide` + `GET /sessions/{id}/memories-used`) live as of Session 117. **Operator UI live as of Session 118**: visit `/dashboard/memories` for the full curation surface, and expand the "Memories used" panel on any `/dashboard/sessions/[id]` page to see exactly which memories shaped that session and vote on each. Remaining: broader E2E tests (Step 8) and the v1.0.0 tag cutover (Sprint 44).

OpsMender is an agent harness. Like every other harness — Claude Code, Aider, OpenAI Assistants — the agent gets better when it has memory that survives across sessions. Without memory, every incident starts cold no matter how many times you've seen it before. Memory is what lets OpsMender accumulate institutional knowledge instead of forgetting it the moment a session ends.

## The 30-second model

1. An operator (or an inbound alert) starts a session on an incident for some service.
2. Before the first `observe` call, the new `recall` node pulls the top-5 most relevant prior lessons for that service + incident and injects them into the agent's system prompt as a `### Past lessons from similar incidents` block.
3. The workflow runs as normal — `observe → diagnose → plan → tier_gate → execute → verify → summarize` — but the agent's first observation is informed by everything it has learned before.
4. If the session completes successfully (no timeout, no failure, no tool errors, non-trivial summary), the new `remember` node distills the session into one short lesson and writes it into `incident_memories`.
5. When per-service memory count crosses 50, the next `remember` call runs one bounded auto-compaction pass to keep the store small.
6. *Coming in Steps 6 + 7:* operators thumbs up / thumbs down each surfaced memory. Retrieval ranking weights `helpful / (helpful + unhelpful)` so unhelpful memories drop out of rotation.

## What a memory looks like

Each memory is one row in `incident_memories`:

| Column | Meaning |
|---|---|
| `title` | Short headline, ≤ 200 chars. Often the symptom and the cause in one phrase. |
| `summary_md` | Markdown body, ≤ 4000 chars. Should cover: what went wrong (symptoms), what turned out to be the cause, what action resolved it, any gotchas. |
| `tags` | 1–5 lowercase hyphenated tags. Severity is typically a tag. |
| `service_id` | The owning service. Used as the primary retrieval scope. |
| `source_incident_id` | The incident this memory came from. Optional — operator-authored memories don't have one. |
| `helpful_count` / `unhelpful_count` | Operator feedback counters. Used to rank surfaced memories. |
| `is_hidden` | Admin can hide a memory without deleting it. Hidden memories never surface and don't count against the compaction threshold. |
| `last_used_at` | Stamped every time the memory surfaces. Used to age out stale lessons in v2. |

## What gets remembered, and what doesn't

By design, memories are conservative:

| Session outcome | Memory written? |
|---|---|
| Workflow `completed` + at least one of summary/diagnosis ≥ 20 chars + no tool errors | **Yes** |
| Workflow `failed` | No |
| Workflow `timed_out` | No |
| Any tool call had an `error` | No |
| Summary and diagnosis both trivial (< 20 chars) | No |
| Tool calls were blocked by tier gate (no errors, just policy) | **Yes** — a Tier-1 block is a successful escalation, not a failure |

The rule: the signal we keep is "this approach worked." If the agent crashed, timed out, or hit tool errors, the session doesn't earn a memory — bad lessons are worse than no lessons.

## Trust boundaries

Memory is a trust-sensitive surface. Several invariants hold by design:

- **Per-org isolated.** A memory from Acme never appears in Globex's prompt. Same multi-tenant boundary as every other Sprint 29 entity — every query goes through an `org_id` filter at the repo layer.
- **Advisory only.** Memory is read-only context for the agent. It cannot bypass tier gates, cannot override `SKILL.md`, and cannot authorize a tool call that would otherwise be blocked. The tier gate runs *after* recall, sees the same plan it would have seen without memory, and enforces the same policy.
- **The agent does not write memory directly.** A dedicated post-session `remember` node runs after `summarize` with a strict JSON-schema-validated output. There is no prompt-injection path from chat or tool output into the memory table — the LLM never gets a "write this to memory" tool.
- **Operators own memory.** Once the `/dashboard/memories` page ships, every memory will be visible, editable, and deletable. Operators can also author memories by hand the same way they curate `SKILL.md`.
- **Audited.** Every recall (which memories were surfaced for which session) and every write goes through the same audit pipeline as MCP tool calls. You can trace which memory shaped which session.

## Auto-compaction

Memory storage is bounded so it never grows without limit. When an org's memory count for a single service exceeds 50, the next `remember` call runs one compaction pass:

1. **Exact-title dedup.** If two memories share an identical normalised title (case and whitespace insensitive), the older one is deleted. Pure SQL, no LLM call.
2. **LLM near-duplicate dedup.** If the post-layer-1 count is still over the threshold, one LLM call lists candidate `{action: "delete", id, reason}` operations across the remaining memories. The dedup is bounded to 5 deletes per pass; ops with invalid ids or non-delete actions are silently ignored.

Compaction is bounded in three ways: one pass per `remember` call, never recursive, and always audit-logged. If compaction fails, the session is unaffected — memory failures are swallowed at the writeback layer.

## The self-improvement loop (Steps 6 + 7)

The retrieval ranking is a composite score:

```
score = 2.0 * service_match
      + 1.0 * tag_overlap
      + 0.5 * keyword_match
      + helpful_ratio_boost
```

Once thumbs feedback ships, the `helpful_ratio_boost` term will scale memories that operators have found useful up the rankings and push memories that consistently mislead off the surface. Memories below a quality threshold (e.g. < 25% helpful with ≥ 4 votes) will auto-hide but never auto-delete — the operator decides when to remove.

## What is *not* in v1 (intentionally deferred to v2)

The locked Sprint 45 scope (D-025 in `REFERENCE.md`) defers four directions to v2 because they need richer guarantees than v1 is ready to make:

- **Service playbooks.** An agent-authored, agent-rewritten "how this service works" document that grows alongside the agent. Deferred because letting the agent edit its own operating manual needs a tier story we have not designed yet.
- **Operator-preference memory.** Per-user "always run `kubectl describe` first" / "prefer dry-run for migrations" rules. Deferred — interesting but adds an additional taxonomy we want to validate against real usage before locking in.
- **pgvector embeddings.** Semantic match ("k8s pod oomkilled" matches "memory pressure on cluster"). Deferred until SQL recall quality is the bottleneck — adds an embedding-model choice, a pgvector dependency, an Alembic extension migration, and embedding-cost on every write.
- **Cross-org memory sharing.** Hard "no" by design. Would violate the multi-tenant isolation that the rest of OpsMender depends on.

## REST API (Step 6 — live as of Session 117)

All routes are org-scoped via the active org dependency. Auth follows the same admin/operator/viewer model as the rest of OpsMender.

| Method | Endpoint | Auth | Notes |
|---|---|---|---|
| `GET` | `/memories?service_id=…&include_hidden=true` | any authenticated | Default returns visible memories only |
| `GET` | `/memories/{id}` | any authenticated | 404 if not in active org |
| `POST` | `/memories` | admin or operator | Tags get lower-cased + trimmed in the route; `service_id` validated against the active org |
| `PUT` | `/memories/{id}` | admin or operator | Set `service_id_set: true` to explicitly null the service binding (otherwise the field is left untouched) |
| `DELETE` | `/memories/{id}` | admin only | 204 on success |
| `POST` | `/memories/{id}/feedback` | admin or operator | Body: `{"helpful": true}` or `{"helpful": false}` |
| `POST` | `/memories/{id}/hide` | admin only | Body: `{"hidden": true}` (or `false` to un-hide) |
| `GET` | `/sessions/{id}/memories-used` | any authenticated | Returns recall trail (memory + surfaced_at + score) for one session |

Operator example — author a memory by hand:

```bash
curl -X POST http://localhost:8000/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Checkout 500s = payments-service connection drop",
    "summary_md": "Symptoms: 500s on /checkout. Cause: payments-service drops idle conns after 30s under load. Fix: enable keepalive on the upstream proxy. Watch for: similar drops on /refund.",
    "tags": ["high", "payments", "checkout"],
    "service_id": "1234-…"
  }'
```

Operator example — thumbs-down a memory that misled you:

```bash
curl -X POST http://localhost:8000/memories/$ID/feedback \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"helpful": false}'
```

Operator example — list what the agent saw on a session:

```bash
curl http://localhost:8000/sessions/$SESSION_ID/memories-used \
  -H "Authorization: Bearer $TOKEN"
```

## Where memory lives in the codebase

| Concern | File |
|---|---|
| ORM models | `backend/db/models.py` — `IncidentMemory`, `IncidentMemoryRecallLog` |
| Repository | `backend/db/repos.py` — `IncidentMemoryRepo`, `IncidentMemoryRecallLogRepo` |
| Schema migration | `backend/db/migrations/versions/g7h8i9j0k1l2_add_incident_memories.py` |
| Retrieval (read side) | `backend/memory/retrieval.py` — `recall_for_session`, `derive_query`, `derive_tags`, `format_memories_as_markdown` |
| Writeback + compaction | `backend/memory/writeback.py` — `should_remember`, `MemoryDraft.from_json`, `remember_for_session`, `maybe_compact` |
| `recall` LangGraph node | `backend/agent/nodes.py` — `_build_recall`, stub `recall` |
| `remember` LangGraph node | `backend/agent/nodes.py` — `_build_remember`, stub `remember` |
| Node order | `backend/agent/graph.py` — `DEFAULT_WORKFLOW_NODE_ORDER` (`recall` first, `remember` last) |
| Live session wiring | `backend/api/session_runner.py` — threads `memory_factory`, `org_id`, `service_id`, `source_incident_id` into `build_graph` |
| Tests | `tests/test_incident_memory_repo.py`, `tests/test_memory_retrieval.py`, `tests/test_memory_writeback.py` |
| Locked decisions | `docs/REFERENCE.md` D-025 |

## See also

- [`docs/REFERENCE.md`](../REFERENCE.md) — locked D-025 decisions in full
- [Operator Guide](operator-guide.md) — incident triage flow that consumes memory
- [Skills Guide](skills-guide.md) — `SKILL.md` is the *operator-authored* counterpart to memory's *agent-authored* lessons
