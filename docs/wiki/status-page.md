# Status Page

OpsMender includes one workspace Status Page at `/status`. It publishes selected services, derived component health, public incident updates, recent resolutions, and subscriber email updates.

## Roles

- Admins configure the page in **Settings → Status Page**.
- Admins and operators publish incident updates when the incident belongs to a configured Status Page component.
- Viewers can read a private Status Page only when they are signed in.

## Setup

1. Open **Settings → Status Page**.
2. Enable the page.
3. Choose `Private` or `Public` visibility.
4. Set the title and optional description.
5. Select the services to publish and reorder them.
6. Save settings and components.
7. Open `/status` to verify the public read surface.

`Private` visibility returns a not-found response to unauthenticated requests. `Public` visibility allows unauthenticated reads.

## Publishing Updates

Open an incident whose service is selected as a Status Page component. Admins and operators see **Status update** on the incident command strip. Each update requires:

- state: `investigating`, `identified`, `monitoring`, or `resolved`
- body: operator-authored update text

Published updates appear in the incident timeline and on `/status`. Confirmed subscribers receive email when SMTP is configured.

## Status Derivation

Component status is derived automatically:

- active approved maintenance covering the service marks the component as `maintenance`
- an open incident with at least one published update contributes impact until its latest published state is `resolved`
- `P0` maps to `major_outage`
- `P1` maps to `partial_outage`
- `P2` and `P3` map to `degraded`
- otherwise the component is `operational`

Overall status is the worst component status using:

`operational < maintenance < degraded < partial_outage < major_outage`

Incidents are hidden from the Status Page until an admin or operator publishes at least one status update.

## Subscribers

Visitors can subscribe from `/status`. OpsMender stores token hashes only and uses double opt-in:

1. visitor submits an email address
2. OpsMender sends a confirmation link when SMTP is configured
3. confirmation marks the subscriber active
4. unsubscribe links delete the subscriber row

Admins can remove subscribers from **Settings → Status Page**.

## Reliability Data

When a Status Page component is linked to an active SLA target with uptime samples, `/status` shows a 90-day uptime strip for that component.

## Verification

- disabled page returns not found
- private page returns not found without a valid signed-in user
- public page renders without authentication
- incidents remain hidden until a status update is published
- maintenance windows affect matching service components
- subscriber confirmation and unsubscribe links work
- status update mutations create audit entries
