# AI Incident Memory

> **Status (2026-06-19):** The continuous memory loop, REST API, operator UI,
> feedback, per-session recall trail, and service-scoped auto-compaction are
> live. Memories do not have approval or hidden states: every saved memory is
> immediately eligible for recall.

OpsMender uses memory to carry successful incident lessons into later AI
sessions. The goal is a low-effort self-improvement loop: resolve an incident,
retain the useful lesson, and automatically give that context to the next
session for the same service.

## The 30-second model

1. Before `observe`, the `recall` node retrieves up to five relevant memories
   for the incident's service and adds them to the agent prompt.
2. The normal governed workflow runs. Memory is context only; it cannot bypass
   a Safety Tier, a skill rule, or an approval requirement.
3. After a genuinely successful session, `remember` distills the result into a
   short lesson and saves it.
4. The saved memory is immediately recallable. There is no review queue,
   approval status, rejection status, or hidden state.
5. Once a service has more than 50 memories, the next write runs one bounded
   compaction pass for that service only. Global memories form their own
   separate compaction group.
6. Helpful/unhelpful feedback influences future retrieval ranking.

## What gets remembered

| Session outcome | Memory written? |
|---|---|
| Completed, useful summary/diagnosis, and no tool errors | **Yes** |
| Failed or timed out | No |
| Any tool call returned an error | No |
| Summary and diagnosis are both trivial | No |
| A policy gate blocked an action without an execution error | **Yes** |

The retained signal is “this session produced a useful outcome,” not merely
“this approach was attempted.”

## Trust and ownership boundaries

- **Organization isolated.** Every query is scoped by `org_id`; memories never
  cross tenants.
- **Service scoped.** Service memories are recalled and compacted within their
  service. Global memories are handled as a separate group.
- **Operator visibility is scoped.** Operators see global memories plus
  memories associated with services owned by their teams.
- **Advisory only.** Memory never grants authority or weakens the tier gate.
- **Structured writeback.** A dedicated post-session node validates the
  generated memory before persistence.
- **Auditable.** Recall logs show which memories shaped each session.
- **Team-owned mutation.** Admins may edit or delete any memory. Operators may
  edit or delete only memories whose service belongs to one of their teams.
  Global memories are admin-only for edit/delete. Viewers are read-only.

## Managing memories

`/dashboard/memories` provides search, service filtering, feedback, creation,
editing, and deletion.

- Select a row with its checkbox. The header checkbox selects every row on the
  current page.
- The **Actions** button activates when one or more rows are selected.
- With one selected row, Actions offers **Edit** and **Delete**.
- With multiple rows, Edit is disabled and the destructive action becomes
  **Delete all**.
- Bulk deletion always confirms the exact number of selected memories.
- A mixed selection containing any memory the user cannot manage cannot be
  partially deleted.
- Row-level Edit and Delete remain available for authorized users.

Deletion is permanent. There is intentionally no hidden/unhidden lifecycle.

## Auto-compaction

Compaction starts only after a single service's memory count exceeds 50:

1. Exact normalized-title duplicates are reduced to the newest memory.
2. If still over the threshold, one bounded LLM pass may remove up to five
   near-duplicates.

The pass is non-recursive and failure-tolerant. A service's compaction query
cannot include memories from another service. Memories with no service
(`service_id = null`) compact only against other global memories.

## Retrieval ranking

The current composite ranking uses service match, tag overlap, keyword match,
and helpful/unhelpful feedback. SQL retrieval is intentional in v1; embeddings
remain deferred until retrieval quality demonstrates a need for them.

## REST API

All routes are scoped to the active organization.

| Method | Endpoint | Authorization | Notes |
|---|---|---|---|
| `GET` | `/memories?service_id=…` | authenticated | Lists ordinary recallable memories |
| `GET` | `/memories/{id}` | authenticated | Includes `can_edit` and `can_delete` |
| `POST` | `/memories` | admin/operator | Creates an immediately recallable memory |
| `PUT` | `/memories/{id}` | admin or owning-team operator | Updates one memory |
| `DELETE` | `/memories/{id}` | admin or owning-team operator | Permanent single delete |
| `POST` | `/memories/bulk-delete` | admin or owning-team operator | Atomic delete; rejects the whole mixed unauthorized selection |
| `POST` | `/memories/{id}/feedback` | admin/operator | Records helpful/unhelpful feedback |
| `GET` | `/sessions/{id}/memories-used` | authenticated | Returns the session recall trail |

Example bulk delete:

```bash
curl -X POST http://localhost:8000/memories/bulk-delete \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"memory_ids":["MEMORY_UUID_1","MEMORY_UUID_2"]}'
```

## Where memory lives

| Concern | File |
|---|---|
| ORM model | `backend/db/models.py` |
| Repository and ranking | `backend/db/repos.py` |
| API schemas/routes | `backend/api/schemas.py`, `backend/api/routes/memories.py` |
| Recall | `backend/memory/retrieval.py` |
| Writeback and compaction | `backend/memory/writeback.py` |
| Dashboard | `frontend/app/dashboard/memories/page.tsx` |
| Locked decisions | `docs/REFERENCE.md` D-025 |

## See also

- [Operator Guide](operator-guide.md)
- [Postmortems](postmortem-guide.md)
- [Skills Guide](skills-guide.md)
