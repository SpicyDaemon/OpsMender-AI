# Microsoft Teams

OpsMender posts incident cards to a Microsoft Teams channel or group chat
through a **Microsoft Teams (Workflows)** Notification Channel. Teams posts
each card with its Workflows app; the card's buttons open OpsMender, where
you sign in and confirm the action.

Status: built and tested against a mocked Workflows endpoint. It has not yet
been checked in a live Teams tenant.

---

## 1. What you get

- **One card per incident change**, in the channel or group chat the workflow
  posts to: created, acknowledged, resolved, escalated, escalation exhausted,
  and a reopen.
- **The incident's facts**: priority, status, severity, service, team, org,
  who holds it, and the first line of the description.
- **Buttons that open OpsMender**: while the incident is open, Acknowledge
  (until someone has), Resolve, Escalate and Start AI session. Each button
  opens OpsMender's confirm page. The person signs in, sees the incident and
  the action, and presses **Confirm**; the action then runs once, under their
  own role. A Viewer cannot act. Each button works once.
- **View in OpsMender** on every card, including resolved ones.

---

## 2. What you do not get

- Buttons that act inside Teams. Every action happens on OpsMender's confirm
  page.
- Edits in place. A posted card is never changed; each change posts a new
  card.
- Threads. Each card is a new post.
- AI session updates. Sessions stay visible in OpsMender, the Inbox and your
  other channels.
- Posts for ownership changes (take, release, assign, reassign, handoff,
  team move). Open View in OpsMender to see who holds the incident now.
- Personal Teams messages, and My Routing stages on this channel. A
  Workflows channel posts to a shared channel; it does not page a person.

The Teams bot, with buttons that act in Teams, is being rebuilt.

---

## 3. Before you start

- Set `OPSMENDER_PUBLIC_URL` to the address people use to open OpsMender
  (for example `https://ops.example.com`). Card buttons point there.
- Prefer a shared service account as the owner of the workflow, not a
  person's own account. A flow stops when its owner leaves.

---

## 4. Create the workflow

1. In Teams, open the channel (or group chat) that should get the cards.
2. Open **More options** on the channel, then **Workflows**.
3. Choose the template **Send webhook alerts to a channel** (or **Send
   webhook alerts to a chat**).
4. Pick the team and channel (or the chat).
5. Choose who can trigger the workflow: **Anyone**, or only people in your
   tenant (see section 6).
6. Save the workflow, then open its trigger, **When a Teams webhook request
   is received**, and copy the URL.

The template needs no premium licence.

Treat the URL like a password: anyone who has it can post to the channel.

---

## 5. Add the channel in OpsMender

1. Go to **Paging**, then **Notifications**, and add a Notification Channel.
2. Pick **Microsoft Teams (Workflows)** as the platform.
3. Paste the workflow URL into **Workflow URL**. OpsMender stores it
   encrypted and never shows it again.
4. Keep the **Respond** lane. Track is not offered: it means one post edited
   in place, which a workflow cannot do.
5. Choose the team scope: workspace-wide, or the teams whose incidents this
   channel should get.
6. Save, then press **Check configuration**. The rows mean:

   | Row | What it checks |
   |---|---|
   | `credentials` | A workflow URL is saved. |
   | `workflow_url` | The URL uses https and its host. A Power Automate host passes; an unknown host warns; an Office 365 connector URL fails, since Microsoft retired those. |
   | `workflow_signature` | Shown only when the URL has no `sig` value and no Microsoft Entra sign-in is set. |
   | `entra` | The Microsoft Entra fields are all filled in, or all empty. |
   | `public_url` | Where card buttons point (`OPSMENDER_PUBLIC_URL`). |
   | `destination` | Cards go to the channel or chat the workflow posts to. |
   | `post_budget` | Posts in the last 5 minutes, and any held cards. |
   | `last_post` | Days since the workflow last accepted a post. |

7. Press **Send live test**. OpsMender posts a small card and says that Teams
   Workflows accepted it.

---

## 6. Optional: Microsoft Entra sign-in

Use this only for a workflow whose trigger allows only people in your tenant.

1. In Microsoft Entra, register a single-tenant app and create a client
   secret. Note its expiry date: posts stop when it expires.
2. Allow the app as a caller of the workflow's trigger.
3. In OpsMender, fill in all three fields: **Microsoft Entra tenant ID**,
   **Application (client) ID** and **Client secret**. The client secret is
   stored encrypted. Saving a new secret replaces the stored credentials, so
   paste the workflow URL again too.

If **Send live test** reports HTTP 401 or 403, set the trigger to **Anyone**
and clear the three fields. Entra sign-in works with the commercial cloud
only, and it has not yet been checked in a live tenant.

---

## 7. Keep it running

- **Add a co-owner** to the workflow. A flow whose owner leaves is orphaned.
- **90 days without a run.** Microsoft may turn a flow off after 90 days
  without a run, and emails its owners. Turn it on again, then press **Send
  live test**. **Check configuration** warns at 80 days.
- **14 days of failures.** Microsoft turns a flow off after 14 days of failed
  runs. Fix the cause, turn it on, then press **Send live test**.

---

## 8. If the URL leaks

1. In the workflow's trigger, choose **Regenerate SAS key**. The old URL stops
   working.
2. Paste the new URL into the channel and save. Replacing the credentials
   replaces the client secret too, so enter it again if you use Entra
   sign-in.
3. Press **Send live test**.

---

## 9. The post limit

Microsoft takes at most 25 posts in 5 minutes per Teams connection. OpsMender
posts at most **20 times in 5 minutes to each Workflows channel**, counted
across every OpsMender process, and keeps the rest of Microsoft's limit for
your other flows.

- Past the limit, an incident change is **held**. When there is room, the
  newest state of each held incident posts as one card, open and higher
  priority incidents first, for up to an hour.
- The incident timeline shows **Held for Teams Workflows**. A held card that
  can no longer post (the channel no longer takes the incident) shows **Not
  posted to Teams Workflows**.
- Use one flow per channel. Several flows of one owner share Microsoft's
  limit, which OpsMender cannot see.

---

## 10. Buttons

- Each button opens OpsMender's confirm page; opening it changes nothing.
- The person signs in and presses **Confirm**; the action runs once.
- A button works for a day by default (`OPSMENDER_ACTION_LINK_TTL_MINUTES`).
- Buttons need `OPSMENDER_PUBLIC_URL`.
- In a shared channel, the first person who uses a button spends it for
  everyone; newer cards carry fresh buttons.

---

## 11. Government clouds

The Flow bot is not available in GCC, GCC High and DoD. Set the workflow's
post action to post as **User** instead. Expect the `workflow_url` row to warn
about the host, since government clouds use other hosts. Entra sign-in is
commercial cloud only.

---

## 12. What "accepted" means

When OpsMender says Teams Workflows **accepted** a card, the workflow took it.
A failure inside the flow afterwards (for example, the flow cannot post to the
channel) shows only in the workflow's run history in Power Automate.

---

## 13. Troubleshooting

| Message | Cause | Fix |
|---|---|---|
| Teams Workflows did not find the workflow (HTTP 404) | The workflow is off or deleted, or its URL was regenerated. | Turn it on, or paste the current URL, then **Send live test**. |
| Teams Workflows refused the caller (HTTP 401 or 403) | The trigger does not allow this caller. | Set the trigger to **Anyone**, or check the Microsoft Entra sign-in. |
| Teams Workflows refused the card (HTTP 400) | The workflow could not read the card. | Recreate the workflow from the **Send webhook alerts to a channel** template. |
| Teams Workflows refused the card as too large (HTTP 413) | The card was over Microsoft's size limit. | Report it; OpsMender keeps cards under 26 KB. |
| Teams Workflows answered with a redirect, which OpsMender does not follow | The URL is not a workflow trigger URL. | Copy the URL from **When a Teams webhook request is received**. |
| Teams Workflows is throttling posts (HTTP 429) | Microsoft's limit was reached, often by other flows of the same owner. | Nothing: OpsMender holds the card and tries again. Use one flow per channel. |
| Teams Workflows did not answer | Microsoft or the network did not answer. | OpsMender holds the card and tries again with a growing wait. After five failures in a row the channel shows as failed; once the workflow answers again, press Check configuration or Send live test to clear it. |
| The Teams Workflows URL must use https | The saved URL is not an https URL. | Paste the URL from the workflow's trigger. |
| The stored workflow URL cannot be read | The server's secret key changed. | Paste the URL again. |
| Microsoft Entra sign-in needs the tenant ID, client ID and client secret together | Some Entra fields are empty. | Fill in all three, or clear them. |
| Microsoft Entra sign-in requires a known Power Automate host | The workflow URL is not on logic.azure.com or powerplatform.com. | Copy the trigger URL from Power Automate. OpsMender sends Entra tokens only to these hosts. |
| Microsoft Entra refused the app credentials | Wrong tenant ID, client ID or client secret, or the secret expired. | Check the app registration and create a new secret if needed. |
| Microsoft Entra did not answer | Microsoft Entra or the network did not answer. | Nothing: OpsMender holds the card and tries again. |

No message ever contains the workflow URL, its path or its signature.

---

## 14. The older Microsoft Teams channel type

The older **Microsoft Teams** channel type still sends nothing. It is kept for
the Teams bot that is being rebuilt; saved ones stay editable, and a page to a
saved Teams contact method is recorded as skipped with the reason
`teams_rebuilding`. Add a Microsoft Teams (Workflows) channel instead. The old
`OPSMENDER_TEAMS_*` environment variables are not read.

---

## 15. Status

Built and tested against a mocked Workflows endpoint. It has not been checked
in a live Teams tenant yet, including how Teams renders the card, whether the
button links survive link scanners and mobile in-app browsers, and whether a
trigger limited to your tenant accepts an app token.

---

See also:

- [Slack as your paging surface](slack-paging-surface.md): pages, cards and verified buttons in Slack.
- [Notification Preferences](notification-preferences.md): channels, routing by priority, quiet hours.
