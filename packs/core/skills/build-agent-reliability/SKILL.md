---
name: build-agent-reliability
description: Harden agent and workflow systems with typed contracts, deterministic boundaries, approval gates, retries, observability, evaluation harnesses, cost controls, and staged QA. Use alongside every build and before calling any automation production-ready.
---

# Build Agent Reliability

Apply this skill to every agentic build. The goal is a boring, inspectable system with bounded failure, not a demo that succeeds once.

## Reliability pass

1. **Define success:** Write human-authored success and failure criteria, representative fixtures, and the definition of done.
2. **Choose boundaries:** Keep fixed steps deterministic. Use agents only for ambiguous interpretation or dynamic tool order. Leave relational and high-risk decisions human-owned.
3. **Type contracts:** Give every trigger, tool, and child workflow explicit inputs, outputs, required fields, enums, and error shapes. Validate before side effects.
4. **Make retries safe:** Add idempotency keys, deduplication, timeouts, retry caps, backoff for transient faults, and no retry for invalid input. Return actionable error feedback to the caller.
5. **Gate actions:** Require human approval before outbound sends, publishing, purchases, destructive changes, or sensitive decisions. Approval must bind to the exact version reviewed.
6. **Observe:** Log run ID, parent and child IDs, input hash, route, tool calls, model, tokens, cost, latency, result, error class, approval, and side effects. Exclude secrets and unnecessary sensitive payloads.
7. **Evaluate:** Maintain a fixed test set plus adversarial cases. Compare prompt/model versions on correctness, groundedness, cost, speed, and failure count.
8. **Scale gradually:** Run shadow or dry-run mode, then test with real inputs at 100, 1,000, and 10,000 runs where volume matters. Watch error counts, not only percentages.
9. **Hand over:** Back up the workflow, document credentials and ownership, record rollback, and provide a client or operator runbook.

## Failure taxonomy

Classify failures as invalid input, missing data, identity mismatch, permission, provider outage, timeout, model error, parser error, policy violation, or human rejection. Each class needs an owner and recovery path. Do not hide a failed run behind a friendly success message.

## Review checklist

- Can a duplicate trigger create a duplicate side effect?
- What happens with an empty, malformed, oversized, stale, or adversarial input?
- Can a child agent loop forever or call the wrong tool?
- Is the source of every claim recoverable?
- Can an operator pause, inspect, replay, or roll back a run?
- Does the cost remain acceptable at expected volume?

Do not label a build production-ready until the evidence, test counts, residual risks, and next monitoring action are written down.

