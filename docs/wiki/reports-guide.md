# Reports & Analytics Guide

Reports and Analytics are read-only views for Admins and Operators.

## Incident Reports

The Reports page exports incident CSV/PDF files and schedules recurring email
delivery for stakeholders. Incident report MTTA and MTTR use the same lifecycle
fields as Analytics.

## Analytics

The Analytics page has two tabs:

- **Noise**: inbound alert volume, created/updated/skipped breakdown, grouped
  alert savings, flapping Incident count, noisiest Services, and alerts by UTC
  hour of day.
- **Response**: MTTA and MTTR overall, by Service, by priority, and by weekly
  trend.

Both tabs support 7/30/90-day presets, an optional Service filter, and CSV
export.

## Metric Definitions

- Inbound alerts are ingest log rows created in the selected time range.
- Incidents created is the count of ingest log rows whose dedup action is created.
- Noise reduction ratio is 1 minus incidents created divided by inbound alerts.
- Grouped alert savings is the sum of correlated alerts recorded on incidents created in the selected range.
- Flapping incident count is the number of incidents created in the selected range that were marked flapping.
- MTTA seconds is the median created-to-acknowledged duration for incidents with acknowledged_at set.
- MTTR seconds is the median created-to-resolved duration for incidents whose status is resolved, using updated_at as the resolved timestamp.
