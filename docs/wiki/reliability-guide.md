# Reliability

OpsMender Reliability provides active uptime and response-time tracking for SLA
targets. Open **Observe → Reliability**, then select a target to see its detail
dashboard.

## What the target detail shows

- Current status, last check, and the last 24 hours.
- Uptime summaries for 7, 30, and 365 days.
- Uptime History as a status-only bar chart with dated X-axis labels and hover
  details. Green is up, red is down, and gray is no data.
- Outage History with start, end, duration, and maintenance classification.
- Response Time for 15m, 30m, 1h, 6h, 12h, or 24h.
- Response Time History for 7d, 30d, 90d, or 365d.

Response-time charts use a solid line for average latency. The panel header also
shows the aggregate average and sample count for the selected window.

## Maintenance and uptime

An active, approved Maintenance Window covers probes by global, service or
team scope. Service-linked targets use their service's team. Legacy windows
listing a target ID or `*` still cover those probes. Roster windows cover pages
through that roster, and do not cover uptime samples.

Covered samples count as up in uptime summaries and SLO percentages, even when
the probe fails. Two up, one down and two maintenance samples report 80% uptime,
one minute of downtime and two minutes of maintenance. OpsMender still records
the actual probe result, latency and maintenance flag for Outage History.

A matching window also prevents new SLO burn incidents while it is active,
including a burn caused by an earlier outage. After it ends, probes and SLO
incident creation resume normally. Pending, expired and unrelated windows do
not suppress those checks. Recurring windows follow the same active periods as
intake and paging. Existing incidents are not closed by a new window.

## Retention

Raw uptime samples contain `latency_ms` and are retained for 30 days. To support
longer history, the downsampler stores exact count-weighted average, minimum,
and maximum latency in 5-minute and 1-hour rollups.

The latency rollup fields were introduced by migration `e0f1a2b3c4d5`.
Pre-migration rollups cannot be backfilled because they never stored latency;
those periods appear as honest gaps. New history accumulates automatically
after migration.

## API

`GET /sla-targets/{target_id}/response-time?window=24h`

Supported windows are `15m`, `30m`, `1h`, `6h`, `12h`, `24h`, `7d`, `30d`,
`90d`, and `365d`.
