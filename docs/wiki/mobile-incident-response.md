# Responding to an incident from your phone

Sprint 38 makes the OpsMender web UI usable on a phone for the most time-sensitive operations: opening the incident detail page from a Slack or Teams push, approving a Tier-1 action, and acknowledging a page from the web. This page is the operator-side guide for that flow.

> Sprint 38 doesn't add native push. The web UI relies on Slack's and Teams's native push to deliver the deep link; once you tap "View in OpsMender" the page loads in your phone's browser and the mobile-optimized layout takes over from there.

---

## 1. The flow

1. Your phone gets a Slack DM (or Teams card) from OpsMender pinging you about an incident.
2. You tap **Acknowledge** in the chat surface, or tap **View in OpsMender** to open the web UI.
3. The incident detail page loads in your mobile browser with a vertical layout: the header card stacks above the action button, sessions stack below, and the paging panel collapses cleanly into the same column.
4. If the AI agent needs a Tier-1 approval, the pending-approval card on the session detail page shows the action context above the **Approve** / **Reject** buttons (instead of beside them on desktop) so one-tap decisions stay easy.

---

## 2. What's mobile-optimized

Sprint 38 reworked three surfaces. Everything else degrades gracefully but isn't actively tuned yet.

| Surface | Mobile behavior |
|---------|-----------------|
| `/dashboard/incidents/detail` | Smaller heading on phones (`text-xl` vs. `text-3xl`). Header padding tightens (`px-4 py-4` vs. `px-6 py-6`). The "Quick View" sidebar drops its 240-px min-width and stacks below the title on screens < lg. The "New Session" button shortens its label to "New" to save bar space. |
| Pending-approval card on `/dashboard/sessions/detail` | Approve / Reject buttons move from a right-hand column to a 2-column grid below the JSON action context on phones, so each button gets ~50% of the screen width and is comfortable to thumb-tap. |
| Escalation chain step editor on `/dashboard/paging` | The fixed `120px / 1fr / 120px / auto` 4-column grid collapses to a single column on phones — every field gets the full width. |

---

## 3. Recommended workflow

For mobile-first responders we recommend:

1. **Enable Slack DM or Teams DM** in your `Paging → My Notifications` preferences. Each surface ships push to your phone — `Push notifications` already work without any extra OpsMender wiring.
2. **Set quiet hours with a P0 breakthrough**. Page yourself only at night for true sev-0 pages so phone fatigue doesn't burn you out.
3. **Use the chat surface for ack / take / resolve**. The web UI is for the long-tail tasks — reading logs, approving a Tier-1 action that needs more context, marking the post-mortem owner.

---

## 4. Verification checklist

Before relying on this in production, a real-device smoke test is the only way to find layout bugs. Run through:

- [ ] Open `https://<your-opsmender-host>/dashboard/incidents/detail?id=<some-incident>&from=slack` in iOS Safari. Confirm the breadcrumb banner renders, the heading isn't clipped, and the "Start Session" button is reachable above the fold on a 390-wide viewport (iPhone 12/13/14 baseline).
- [ ] Repeat in Android Chrome at 360px wide (the Pixel baseline).
- [ ] Trigger a Tier-1 approval and confirm the pending-approval card stacks vertically: JSON context on top, Approve + Reject side-by-side below.
- [ ] On the paging page, open the Escalation Chain step editor. Confirm Type / Target / Timeout each take the full screen width on a phone.
- [ ] Tap **View in OpsMender** from a real Slack page card. Confirm the detail page loads with the `?from=slack` breadcrumb.

If any of these fail, file an issue with a screenshot — most of the remaining mobile cleanup will be incremental tuning of Tailwind breakpoints.

---

## 5. What's *not* mobile-optimized yet

These surfaces still target tablet+ and may show overflow on phones:

- `/dashboard/config` (operator setup, not a runtime path).
- `/dashboard/organizations` (super-admin only).
- `/dashboard/skills`, `/dashboard/scans` (authoring surfaces).
- The full session-detail split view (event stream + co-pilot chat) is usable but cramped on phones. Use the incident-detail page's "Open in sidecar" surface on tablet+ for the rich view.

These will land iteratively. The Sprint 38 goal was the **respond-to-a-page** path, not the full SRE console on a phone.

---

See also:

- [Slack as your paging surface](slack-paging-surface.md) — push delivery + button actions in Slack.
- [Teams as your paging surface](teams-paging-surface.md) — push delivery + adaptive cards in Teams.
- [Notification Preferences](notification-preferences.md) — channels, per-priority routing, quiet hours.
