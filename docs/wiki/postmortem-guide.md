# Postmortems

Every resolved or closed incident in OpsMender has a postmortem surface attached to it. The button lives on the **Incident Command Strip** at the top of each incident detail page, labelled **Create postmortem** — clicking it opens a per-incident editor at `/dashboard/incidents/postmortem?id=<incident_id>`.

This guide covers how the editor works, the recommended section structure, who is allowed to edit, and how postmortems feed back into the AI's incident memory.

---

## When the button shows up

The **Create postmortem** action is part of the Incident Command Strip and is **only visible once the incident reaches `resolved` or `closed`**. While an incident is still `open` or `in_progress`, the strip shows lifecycle actions (Acknowledge / Take over / Start session / Resolve) instead. This keeps the surface focused on what the operator needs in the moment: live response while it's happening, retrospective once the bleeding has stopped.

The editor itself remains reachable by direct URL on any incident regardless of status — the visibility rule is just a UX nudge, not a backend gate.

---

## The editor

The editor is split into a main pane and a right rail.

### Main pane — Edit / Preview

The toolbar carries an **Edit / Preview** segmented toggle.

- **Edit** mode is a full-height monospace Markdown textarea. Write Markdown directly — `##` for section headings, `-` for bullets. Standard CommonMark is fine.
- **Preview** mode renders a lightweight pass over the same draft so you can verify the structure before saving. The renderer is deliberately minimal (H2 headings + paragraphs + bullet lists) — it's a structural check, not a full Markdown surface.

The textarea is spell-checked. Resize it vertically if you need more room.

### Right rail — section template and tip

The right rail lists the seven canonical sections OpsMender recommends with a one-line hint for each, plus a **Reset to template** button that replaces the current draft with the empty section template. If your draft is non-empty, **Reset to template** prompts before discarding.

A second card carries a tip about the **Memory candidates** section — see [How postmortems feed memory](#how-postmortems-feed-memory) below.

### Save, Clear, dirty state

The toolbar carries three actions:

| Action | When it's enabled | What it does |
|--------|-------------------|--------------|
| **Save** | Only when the draft differs from what is stored | Writes the current draft to `incidents.postmortem_md` and stamps `incidents.postmortem_updated_at`. |
| **Clear** | Only when a stored postmortem exists | Confirms, then writes `null` to both fields and resets the editor to the empty section template. |
| **Back to incident** | Always | Returns to the incident detail page. |

There is no autosave. If you navigate away with unsaved changes, the next session starts from the stored value (or the template, if none is stored).

---

## Recommended sections

The default template, returned by `GET /incidents/{id}/postmortem`, ships these seven section headings. They follow the standard SRE postmortem shape — keep them, reorder them, or remove what isn't relevant; the backend doesn't enforce the structure, it just suggests it.

| Section | What to write |
|---------|---------------|
| **Summary** | What happened, in one paragraph. The reader should be able to grok the incident from this alone. |
| **Impact** | Who was affected, for how long, and how badly. Numbers help — affected users, error rate, latency p99, dollars. |
| **Timeline** | Key moments in UTC. Pull from the Incident Timeline on the detail page (alert opened, acknowledged, mitigated, resolved). One bullet per moment. |
| **Root cause** | The underlying technical cause. Resist the urge to stop at "human error" — keep asking why. |
| **Resolution** | What you changed to stop the bleeding, and what's still in flight (e.g. a follow-up PR, a config rollout, a capacity request). |
| **Lessons learned** | What worked, what didn't, what to change for next time. This is the part the team revisits in 6 months. |
| **Memory candidates** | Short, durable lessons to save into OpsMender's incident memory. One bullet per memory. |

---

## How postmortems feed memory

The **Memory candidates** section is the bridge between a postmortem and OpsMender's AI incident memory ([D-025](../REFERENCE.md), [memory-guide.md](memory-guide.md)). Bullets in that section are intended as *durable lessons* you want the agent to recall the next time a similar incident fires.

In the v1 surface, memory candidates are **operator-curated**: the agent does not automatically scrape this section. After writing the postmortem, copy each candidate over to `/dashboard/memories` as a new memory tied to the relevant service. The session detail page's "Memories used" panel will then show the new lesson the next time it shapes an agent decision.

A future iteration will offer one-click "promote to memory" on each bullet here. Until then, treat memory candidates as your shortlist for the curation surface.

**Keep candidates short.** A memory that fits in one line is far more likely to land usefully in a future prompt than a paragraph. Keep them project-agnostic too — "Postgres autovacuum tuning matters under bulk writes" carries across incidents; "the migration on 2026-05-26 failed" doesn't.

---

## Who can edit

| Role | Read | Write |
|------|------|-------|
| **admin** | ✓ | ✓ |
| **operator** | ✓ | ✓ |
| **viewer** | ✓ | — (403) |

Viewers see the postmortem read-only inside the editor (the Save / Clear actions don't render).

---

## API surface

If you'd rather author postmortems outside the UI (a CI script that generates a draft from the incident's audit log, for example), the REST surface is two routes:

- `GET /incidents/{id}/postmortem` — returns the stored markdown, last-edit timestamp, and the canonical section template.
- `PUT /incidents/{id}/postmortem` — body `{"postmortem_md": "..."}`. Pass an empty or whitespace-only string to clear.

Both routes require the same authentication as the rest of the API. PUT requires the `admin` or `operator` role.

---

## Storage and retention

Postmortems are stored as plain Markdown in `incidents.postmortem_md` with a separate `incidents.postmortem_updated_at` so the UI can show "last edited" independently of the incident's own lifecycle clock.

Postmortems live as long as the incident does — the data retention surface ([Config → Storage & retention](admin-guide.md)) prunes incidents and their postmortems together. Memories curated from a postmortem are governed by **memory retention**, which is operator-curated and never auto-deletes.

---

## Tips

- **Write while the context is fresh.** The best postmortems are drafted within 24 hours, while the incident timeline is still vivid. Pull from the incident's own [timeline view](operator-guide.md) for accurate UTC timestamps.
- **One memory candidate per line.** It makes curation easier and forces you to keep each lesson focused.
- **Resist hindsight bias.** Frame the root cause and lessons around what was knowable at the time, not what is obvious in retrospect.
- **Link to artifacts, don't paste them.** Logs, dashboards, and PRs belong as Markdown links — the postmortem is a narrative, not a log dump.
