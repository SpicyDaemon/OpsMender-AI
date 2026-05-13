# Detectors Guide

Detectors in Opsmender are proactive, LLM-driven rules that continuously monitor your incoming metrics, logs, or signals to identify potential incidents before they cause widespread outages. 

Unlike traditional static thresholds (e.g., `CPU > 90%`), Detectors use AI to evaluate the *context* and *semantics* of the data.

## 1. Creating a Rule

1. Navigate to the **Detectors** tab in the dashboard.
2. Click **New Detector**.
3. **Name:** Give your detector a clear name (e.g., "Identify Sudden Spikes in 5xx Errors").
4. **Prompt:** Write the evaluation criteria (see Prompt Design below).
5. **Schedule:** Define how often the detector should run (e.g., every 5 minutes).

## 2. Prompt Design

The prompt is the core of your detector. It instructs the LLM on what to look for and what constitutes an anomaly.

**Good Prompt Example:**
> "Analyze the attached access logs. Look for sudden bursts of 500, 502, or 503 HTTP status codes originating from the same IP or targeting the same endpoint. Ignore sporadic 404 errors. If you detect a coordinated burst of 5xx errors, trigger an alert and extract the targeted endpoint."

**Bad Prompt Example:**
> "Tell me if there are errors." *(Too vague; will result in high false positives)*

## 3. Interpreting History

Every time a detector runs, Opsmender logs the result in the detector's **Run History**.
- **Pass:** The AI evaluated the data and found no issues.
- **Triggered:** The AI detected an anomaly matching your prompt and ingested a new Incident into the system.
- You can inspect the historical runs to see the exact payload the AI received and its reasoning.

## 4. False-Positive Handling

LLM-based detection can occasionally trigger false positives. Opsmender provides tools to fine-tune your detectors:

1. **Review the Reasoning:** Look at the Run History for the false positive. Read the AI's explanation for *why* it triggered.
2. **Refine the Prompt:** Add explicit exclusion clauses to your prompt.
   - *Example:* "Ignore errors related to the `/healthz` endpoint."
   - *Example:* "Do not trigger unless the error rate exceeds 10 occurrences in the provided window."
3. **Adjust the Context Window:** If the AI is triggering on stale data, reduce the time window of data fed into the detector.
