# Microsoft Teams: being rebuilt

OpsMender sends nothing to Microsoft Teams right now. Teams delivery is being
rebuilt. Until it is ready, use Slack, email, SMS or voice, or another chat
channel for pages and incident updates.

---

## 1. Why

The Teams support OpsMender shipped earlier does not work as built:

- Microsoft lets an app post Teams chat messages through Microsoft Graph with
  app-only access only during a migration. It is not a way to send pages.
- Teams card buttons that act, such as Acknowledge or Resolve, need a Teams
  bot. A card posted through Microsoft Graph can only carry link buttons.
- The Office 365 connector webhooks that posted the older Teams message cards
  are retired.

A page that looks sent but never arrives is worse than no page. So OpsMender
sends nothing to Teams until the new path is ready, and says so.

---

## 2. What happens today

- **Nothing is sent to Teams.** No pages, incident cards, status updates or AI
  session posts.
- **Check configuration fails.** On a Teams Notification Channel, **Check
  configuration** shows a failing `platform` check that gives the reason.
  **Send live test** stops at that check, so no test message is posted.
- **Pages are recorded as skipped, never as sent.** A page routed to a saved
  Teams contact method (`teams_dm` or `teams_dm_graph`), or a My Routing stage
  that uses a Teams Notification Channel, is recorded as skipped with the
  reason `teams_rebuilding`. The next stage still fires after its wait.
- **Your setup is kept.** Saved Teams Notification Channels and Teams contact
  methods are not deleted. You can still create a Teams Notification Channel,
  but its native actions stay off and it sends nothing.
- **Buttons on old Teams cards change nothing.** On a Teams card posted
  earlier, Acknowledge, Resolve, Escalate and Start AI Session answer with the
  reason and leave the incident as it is. Act in OpsMender or another channel
  instead.
- **The API says the same.** The Teams platform capabilities report
  `delivery: false` and an `unavailable_reason` with the reason.
- **The Teams environment variables are ignored.** OpsMender no longer reads
  `OPSMENDER_TEAMS_WEBHOOK_URL`, `OPSMENDER_TEAMS_GRAPH_TENANT_ID`,
  `OPSMENDER_TEAMS_GRAPH_CLIENT_ID` or `OPSMENDER_TEAMS_GRAPH_CLIENT_SECRET`.

---

## 3. What to use meanwhile

Move any My Routing stage that points at Teams to a channel that delivers
today:

- **Slack**, with verified Acknowledge, Resolve, Escalate and Start AI Session
  buttons. See [Slack as your paging surface](slack-paging-surface.md).
- **Email**, through SMTP or Mailgun.
- **SMS or voice**, set up under **Settings -> Voice & SMS calling**.
- **Another chat channel**, such as Discord, Google Chat, Telegram, Mattermost
  or Matrix.

Check My Routing for P0 and P1. Each should have at least one stage that is
not Teams, since a Teams stage is skipped.

---

## 4. What is coming

Teams support comes back in two steps:

1. **A Teams Workflows connector.** OpsMender posts cards to Teams through
   Teams Workflows. The card buttons open OpsMender, where you confirm the
   action on a signed page.
2. **A Teams bot.** Card buttons that act in Teams itself.

Saved Teams Notification Channels and contact methods stay in place for both.
This page will cover setup once the connector is ready.

---

See also:

- [Slack as your paging surface](slack-paging-surface.md): pages, cards and verified buttons in Slack.
- [Notification Preferences](notification-preferences.md): channels, routing by priority, quiet hours.
