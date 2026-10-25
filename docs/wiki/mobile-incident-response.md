# Responding to an incident from your phone

Sprint 38 makes the OpsMender web UI usable on a phone for the most time-sensitive operations: opening the incident detail page from a Slack or Teams push, approving a Tier-1 action, and acknowledging a page from the web. This page is the operator-side guide for that flow.

> Sprint 38 doesn't add native push. The web UI relies on Slack's and Teams's native push to deliver the deep link; once you tap "View in OpsMender" the page loads in your phone's browser and the mobile-optimized layout takes over from there.

---

## 1. The flow

1. Your phone gets a push from Slack, Teams or Discord: a DM page from OpsMender, or the incident card in your team's Notification Channel.
2. The channel card says what broke and who holds it: Service, Team, Responder (who it is waiting on) and, once someone acknowledges, Acknowledged by.
3. You tap **Acknowledge** on the card. You get a short confirmation (on Slack and Discord only you can see it), and the card updates for everyone: it names you as the acknowledger and the Acknowledge button is gone, while Resolve, Escalate and Start AI Session stay. Slack and Discord edit the card in place; Teams posts a fresh card in the new state. If you release the incident later, Slack and Discord edit the card again so it offers **Acknowledge** to the next person. Or tap **View in OpsMender** to open the web UI.
4. The incident detail page loads in your mobile browser with a vertical layout: the sticky command strip stays reachable near the top, the incident timeline stacks below the header card, and the context rail collapses cleanly into the same column.
5. If the AI agent needs a Tier-1 approval, the pending-approval card on the session detail page shows the action context above the **Approve** / **Reject** buttons (instead of beside them on desktop) so one-tap decisions stay easy.

---

## 2. What's mobile-optimized

Sprint 38 reworked three surfaces. Everything else degrades gracefully but isn't actively tuned yet.

| Surface | Mobile behavior |
|---------|-----------------|
| `/dashboard/incidents/detail` | Smaller heading on phones (`text-xl` vs. `text-3xl`). Header padding tightens (`px-4 py-4` vs. `px-6 py-6`). The sticky command strip keeps the top actions reachable during scroll, the incident timeline becomes one vertical feed instead of separate session cards, and the timeline header shortens the "New Session" label to "New" on small screens. |
| Pending-approval card on `/dashboard/sessions/detail` | Approve / Reject buttons move from a right-hand column to a 2-column grid below the JSON action context on phones, so each button gets ~50% of the screen width and is comfortable to thumb-tap. |
| Escalation chain step editor on `/dashboard/paging/escalation-chains` | The fixed `120px / 1fr / 120px / auto` 4-column grid collapses to a single column on phones: every field gets the full width. |

---

## 3. Recommended workflow

For mobile-first responders we recommend:

1. **Enable Slack DM or Teams DM** in `Paging → Notifications`. Each surface ships push to your phone: `Push notifications` already work without any extra OpsMender wiring.
2. **Set quiet hours with a P0 breakthrough**. Page yourself only at night for true sev-0 pages so phone fatigue doesn't burn you out.
3. **Use the chat surface for ack / take / resolve**. The web UI is for the long-tail tasks: reading logs, approving a Tier-1 action that needs more context, marking the post-mortem owner.

---

## 4. Verification checklist

Before relying on this in production, a real-device smoke test is the only way to find layout bugs. Run through:

- [ ] Open `https://<your-opsmender-host>/dashboard/incidents/detail?id=<some-incident>&from=slack` in iOS Safari. Confirm the breadcrumb banner renders, the heading isn't clipped, and the "Start Session" button is reachable above the fold on a 390-wide viewport (iPhone 12/13/14 baseline).
- [ ] Repeat in Android Chrome at 360px wide (the Pixel baseline).
- [ ] Trigger a Tier-1 approval and confirm the pending-approval card stacks vertically: JSON context on top, Approve + Reject side-by-side below.
- [ ] On the paging page, open the Escalation Chain step editor. Confirm Type / Target / Timeout each take the full screen width on a phone.
- [ ] Tap **View in OpsMender** from a real Slack page card. Confirm the detail page loads with the `?from=slack` breadcrumb.
- [ ] Tap **Acknowledge** on a Slack channel card from the phone. Confirm only you see the confirmation, and the card in the channel now reads `Acknowledged by <you>` and shows Resolve, Escalate and Start AI Session but no Acknowledge.

If any of these fail, file an issue with a screenshot: most of the remaining mobile cleanup will be incremental tuning of Tailwind breakpoints.

---

## 5. What's *not* mobile-optimized yet

These surfaces still target tablet+ and may show overflow on phones:

- `/dashboard/config`, AI Agent setup pages (`/dashboard/models`, `/dashboard/mcp-servers`), and advanced setup aliases such as `/dashboard/ingest-tokens`: operator setup, not a runtime path.
- `/dashboard/organizations` (super-admin only).
- `/dashboard/skills` (an authoring surface).
- The full session-detail split view (event stream + co-pilot chat) is usable but cramped on phones. Use the incident-detail page's "Open in sidecar" surface on tablet+ for the rich view.

These will land iteratively. The Sprint 38 goal was the **respond-to-a-page** path, not the full SRE console on a phone.

---

See also:

- [Slack as your paging surface](slack-paging-surface.md): push delivery + button actions in Slack.
- [Teams as your paging surface](teams-paging-surface.md): push delivery + adaptive cards in Teams.
- [Notification Preferences](notification-preferences.md): channels, per-priority routing, quiet hours.
