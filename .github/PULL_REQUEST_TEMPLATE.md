<!-- Thanks for the PR. Fill out each section — the clearer the context, the faster the review. -->

## Summary

<!-- What does this PR change, and why? One paragraph. -->

## Type of change

<!-- Check all that apply. -->

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that changes existing behavior)
- [ ] Documentation only
- [ ] Refactor / internal cleanup (no behavior change)
- [ ] Build / CI / tooling

## Linked issue

<!-- e.g. "Closes #123" or "Related to #456" -->

## How I tested this

<!-- Commands you ran, manual flows you exercised, screenshots of the UI. -->

```
uv run python -m pytest -q
cd frontend && npm run build
```

## Checklist

- [ ] `uv run python -m pytest -q` passes locally
- [ ] `cd frontend && npm run build` passes locally (if frontend changed)
- [ ] Added or updated tests covering the behavior change
- [ ] Updated `docs/PROMPT_CONTEXT.md` if the change affects architecture, data model, or a locked decision
- [ ] Added an entry to `CHANGELOG.md` under `[Unreleased]` for user-visible changes
- [ ] No secrets committed (API keys, tokens, DB passwords, webhook URLs with embedded credentials)

## Architecture guardrails

<!-- Confirm you haven't crossed any of these. If you have, please flag it explicitly in the summary. -->

- [ ] Did not weaken or bypass the programmatic tier gate
- [ ] Did not add a provider-specific native integration for infrastructure access (MCP-only)
- [ ] Did not remove or reduce audit logging for a tool call, approval, or state transition
- [ ] Did not introduce autonomous parallel execution branches in the workflow

## Notes for reviewers

<!-- Anything non-obvious: tradeoffs considered, follow-up work intentionally deferred, areas you want extra eyes on. -->
