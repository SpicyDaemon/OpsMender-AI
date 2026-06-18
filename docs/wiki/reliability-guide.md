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

Response-time charts use a line for average latency and a shaded min–max band.
The panel header also shows aggregate average, minimum, maximum, and sample
count for the selected window.

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
