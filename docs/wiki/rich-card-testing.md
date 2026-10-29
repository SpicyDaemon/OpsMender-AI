# Rich-card testing: Slack and Teams

Use this procedure alongside [Slack setup](slack-paging-surface.md) and
[Teams Workflows setup](teams-paging-surface.md). A successful submission, a visible
card, a successful action and a completed AI session are separate observations.

## Scope and preparation

Lifecycle coverage includes Slack, Teams, WhatsApp, Discord and Telegram.
This guide defines the current Slack and Teams rich-card checks. Discord's
provider-specific procedure will be added after its acceptance work; do not
claim it passed from another provider's results. WeChat awaits an owner decision.

Use a dedicated authorized test destination, synthetic incidents, isolated
data and disposable resources. Record product head, local changes, image,
transport, client/device, tier, priority, fixture and expected outcomes before
acting. Inspect earlier receipts and reuse them only for their recorded scope.
Keep local test derivatives private and preserve the original populated data.
Record each fixture and provider in the acceptance report.

Keep credentials, tokens, webhook/signed-action URLs, environment files,
identities and raw logs out of committed evidence. Review complete screenshots
before committing. Record generalized results without identifying private tenants or disclosing
credential-bearing connection details.

## Common acceptance steps

1. Verify configuration and actual rendering separately. Check title, priority,
   severity, status, service, team, owner, acknowledgement and fallback text.
   Exercise long text, special characters, missing optional fields and narrow
   layouts. Actual mobile devices remain unverified until tested on a device.
2. Exercise every available control: Acknowledge, Resolve, Escalate, Start AI
   Session and View. Check the visible response, stored incident/chain/owner,
   durable action audit and resulting card. Do not infer execution from prose.
   Open View both signed in and signed out. After sign-in, confirm the same
   incident or session detail opens, including its source label. Cover an
   expired session and configured MFA, SSO or required password changes;
   authentication and current permissions still apply before viewing it.
3. Test unlinked/inactive users, viewer role, wrong handling team, current-role
   changes, disabled channel, duplicate clicks, stale cards and closed incidents.
   Denials must preserve stored state and avoid revealing another team's facts.
4. Verify ownership, release/take, handoff, service/team moves, resolve/reopen
   and the actual provider's edit/follow-up behavior. Confirm stale or moved-away
   cards cannot mutate unauthorized state.
5. Run real sessions at Tiers 0, 1 and 2 with an isolated MCP fixture. Compare
   service/session tier, permitted/blocked tool records, required approvals,
   approve/reject/redirection, resulting tool effects and completion delivery.
   Keep P0-P3 coverage explicit. A model declining to request a write does not
   prove the approval path. Use controlled cases to exercise it separately.
6. Test connection failure, provider rejection, throttling, recovery and
   repeated delivery. Observe failure audit and remaining destinations. Do not
   claim automatic retry unless implemented and verified. Keep ambiguous send
   outcomes distinct from known pre-send connection failures.
7. Produce a separate report for each step: expected, observed, method,
   source/image, safe evidence, bugs, fix location, rerun and remaining limits.
   End with cleanup: revoke/remove test app access, remove temporary workflows
   and tunnels, archive disposable channels, stop lab listeners, clear local
   credentials, restore temporary settings and verify original data unchanged.
   Leave owner-managed channel removal to the owner when instructed.

## Slack

Test Block Kit native callbacks and six registered paging commands:
`/ack`, `/take`, `/release`, `/resolve`, `/snooze-escalation` and
`/opsmender-status`. Cover explicit IDs, qualifying ID-less selection, no
qualifying page, malformed IDs/durations and the seven-day snooze limit.
Use private responses for action outcomes; confirm the original message is
edited where supported and its controls match current state.

Exercise Request URL and Socket Mode independently. For HTTP, test signatures,
timestamp/replay rejection and prompt acknowledgement. For Socket Mode, test
envelope acknowledgement, real connection, token/app mismatch, lease ownership,
restart/reconnect and takeover. Generic Events API messages are not supported
by the current Socket handler and require separate HTTP Events acceptance.
Test public/private notification, personal paging and optional incident channels.

Slack requires prompt acknowledgement of interactions, within three seconds;
business work and user-facing results must not delay that acknowledgement.
See [Slack interaction handling](https://docs.slack.dev/interactivity/handling-user-interaction/)
and [Block Kit](https://docs.slack.dev/block-kit/). These platform documents do
not establish that a local implementation or a particular test passed.

## Teams Workflows

Test the shipped Workflows Adaptive Card surface. Its actions open signed
OpsMender links, with preview/confirmation and current identity/permissions;
they are not the proposed Teams bot callbacks. Verify preview does not mutate,
confirmation acts once, used/expired/tampered links fail, current authorization
is rechecked and View opens the correct incident. Verify subsequent state is
posted according to Workflows' supported behavior rather than assuming Slack's
in-place edit semantics. Cover held/coalesced cards, throttling and recovery
using controlled endpoints where real failure injection is inappropriate.

Consumer Teams does not establish Workflows acceptance. Proposed Teams bot and
personal-message features remain separate. The completed generalized Workflows
reports establish their recorded bounded scope; rerun affected cases when shared
code changes, without repeating unrelated live setup.

Microsoft documents that workflows have user owners and that workflow webhooks
can post Adaptive Cards to channels/chats. Ownership and continuity therefore
need their own deployment check; team membership alone is insufficient proof.
See [Microsoft's webhook and Workflows guide](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook).
