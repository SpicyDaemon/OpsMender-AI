import type { ApprovalEvidence } from "@/lib/types";

const OUTCOME_LABEL: Record<string, string> = {
  ok: "returned a result",
  failed: "failed",
  blocked: "blocked, did not run",
};

function parameterText(parameters: Record<string, unknown> | undefined): string {
  const entries = Object.entries(parameters ?? {});
  if (entries.length === 0) return "";
  return entries
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(", ");
}

/**
 * Shown beside an approval request a session made in a later planning round
 * (M1-166). That request was planned from what the session's tools returned,
 * so the operator is told so and sees the calls made before it, with each
 * call's outcome from the record, and what an operator already rejected in
 * the session. Renders nothing for a first plan's request.
 */
export function ApprovalEvidenceNote({
  evidence,
}: {
  evidence: ApprovalEvidence | null | undefined;
}) {
  if (!evidence || evidence.source !== "tool_results") return null;
  const calls = evidence.calls ?? [];
  const addressed = evidence.addressed ?? [];
  const rejected = evidence.rejected ?? [];
  const notShown = evidence.calls_not_shown ?? 0;

  return (
    <div
      role="note"
      aria-label="Where this request came from"
      className="mt-3 rounded-lg border border-status-medium-border bg-status-medium-bg/40 p-3 text-xs text-fg-secondary"
    >
      <p className="font-semibold text-fg-primary">Written after reading tool results</p>
      <p className="mt-1">
        The session planned this request in round {evidence.round} from what its tools
        returned. Tool output comes from outside OpsMender, so check the request against
        the calls below before you approve it.
      </p>
      {addressed.length > 0 && (
        <p className="mt-2 font-medium text-status-high">
          A result contains text addressed to the assistant:{" "}
          {addressed
            .map(
              (item) =>
                `${item.tool_name} (${item.phrases.map((phrase) => `"${phrase}"`).join(", ")})`,
            )
            .join("; ")}
          .
        </p>
      )}
      {rejected.length > 0 && (
        <p className="mt-2 font-medium text-status-high">
          An operator already rejected in this session:{" "}
          {rejected
            .map((item) => {
              const parameters = parameterText(item.tool_parameters);
              return parameters ? `${item.tool_name} (${parameters})` : item.tool_name;
            })
            .join("; ")}
          .
        </p>
      )}
      {calls.length > 0 && (
        <ul className="mt-2 space-y-1" aria-label="Calls made before this request">
          {calls.map((call, index) => {
            const parameters = parameterText(call.tool_parameters);
            return (
              <li key={`${call.tool_name}-${index}`} className="break-words">
                <span className="font-mono text-fg-primary">{call.tool_name}</span>
                {parameters && <span className="font-mono"> ({parameters})</span>}
                <span>: {OUTCOME_LABEL[call.outcome] ?? call.outcome}</span>
              </li>
            );
          })}
        </ul>
      )}
      {notShown > 0 && (
        <p className="mt-2 text-fg-muted">
          {notShown} earlier call{notShown === 1 ? " is" : "s are"} listed in the session&apos;s
          Tool Activity.
        </p>
      )}
    </div>
  );
}
