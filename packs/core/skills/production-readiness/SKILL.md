---
name: production-readiness
description: Use before a service or major feature reaches a pilot practice — a 15-check gate adapted from AWS's Operational Readiness Review and Google's SRE launch checklist, covering timeouts, idempotency, migrations, N+1 queries, load testing, SLOs, degraded-mode behavior, feature flags, rate limits, queue safety, twelve-factor config, chaos drills, and AI-assisted code discipline. Each check states how to verify it and what counts as evidence. Run after healthcare-app-blueprint's foundations and backend-architecture's service design are in place, and after self-attack's adversarial pass.
---

# Production readiness: the 15-check gate before a pilot

A demo that works for the person who built it is not evidence a pilot will survive a second practice, a slow
payer API, or a redeployed dependency. This gate is adapted directly from AWS's Operational Readiness Review
(a checklist distilled from real incident post-mortems, re-run before every major launch) and Google's SRE
launch-checklist discipline, translated to a Django/Postgres backend and a Next.js frontend on AWS. Run it
before a service or a major feature reaches customers, not only once at the very first launch.

Each row states what to check, how an agent verifies it, and what a human still has to decide. A check marked
"human required" is not something a test can pass on its own — the agent's job is to confirm the artifact
(document, sign-off, drill record) exists, not to make the judgment call itself.

## The 15 checks

| # | Check | How to verify | Evidence |
|---|---|---|---|
| 1 | Every external call (HTTP, DB, queue, third-party API, LLM) has an explicit timeout and bounded retries with backoff and jitter. | Lint or grep for `requests`/`httpx`/`fetch`/`axios` calls with no `timeout=`; grep Celery task decorators for missing `max_retries`/`retry_backoff`. | A CI job that fails the build on a match; zero matches on the current codebase. |
| 2 | A dependency that can cascade a failure (payer APIs, LLM calls, cross-service calls) is wrapped in a circuit breaker that fails fast past a threshold. | Confirm a breaker library or decorator wraps each such call. | Code reference plus the threshold value; a human sets the threshold. |
| 3 | Every state-changing endpoint, webhook handler, and queue consumer is idempotent. | Run the duplicate-delivery test required by backend-architecture section 3. | The test passes and is committed, named after the endpoint it covers. |
| 4 | Migrations on large or hot tables use zero-downtime-safe operations. | Run migrations through the zero-downtime backend in CI; check the migration diff against the banned-operation list in backend-architecture section 6. | A green CI run on the migration; no banned operation present. |
| 5 | No N+1 queries: list and detail views have `assertNumQueries` regression tests, and an N+1 detector runs in the suite. | Run `django-zeal` or `nplusone` as part of `pytest`; confirm `assertNumQueries` exists for the changed views. | The detector reports zero N+1 queries on the changed views; the query-count test is committed. |
| 6 | A load test exists for every new public or high-traffic endpoint, with k6 or Locust thresholds wired into CI as pass/fail. | Confirm a k6/Locust script exists for the endpoint and that CI enforces its p95-latency and error-rate thresholds. | The CI job and its threshold values; a human sets the traffic model and the numeric target from the service's SLO. |
| 7 | Each service or feature has a written SLO (latency, availability, correctness) and an error-budget policy. | Confirm the SLO document exists and is linked from the service's README or `docs/`. | The document; a human sets the actual target numbers and the burn-policy action. |
| 8 | This 15-check gate is completed and signed off before the service or feature reaches customers, covering on-call ownership, runbook, rollback plan, and alerting coverage. | Confirm a completed checklist artifact exists in the repo for this launch. | Human sign-off recorded with a name and date; the agent verifies the artifact exists, not that the judgment was correct. |
| 9 | Critical-path features keep working in a degraded mode when a downstream dependency is unavailable; no request silently hangs with no fallback. | Grep for external calls with no `except`/fallback branch on the critical path; confirm a cached or skeleton render exists for slow-backend cases on the frontend. | A list of flagged call sites with their fallback behavior; a human confirms the fallback is acceptable. |
| 10 | Feature flags gate new or risky functionality and are cleaned up on an agreed TTL. | Lint the flag registry for age; flag anything past the TTL with no keep/remove decision recorded. | The CI lint output; zero stale flags without a decision. |
| 11 | Every public and partner-facing endpoint has a per-tenant rate limit with a defined 429 and `Retry-After`. | Confirm the rate-limiting middleware is applied to every public route in the URL configuration. | The applied-routes list; a human sets the numeric threshold per endpoint. |
| 12 | Every queue consumer has bounded concurrency, a dead-letter queue, and is idempotent. | Check the infrastructure-as-code for a `RedrivePolicy` (or equivalent) on every queue; cross-reference check 3 for idempotency. | The infra diff showing a DLQ on every queue the change touches. |
| 13 | Twelve-Factor compliance: config only from environment or secrets manager, the app is stateless, logs go to stdout as structured events. | Static scan for hardcoded config values, disk writes outside `/tmp`, and log calls not routed to stdout. | The scan output with zero findings on the changed code. |
| 14 | A supervised, staging-only chaos or failure-injection drill runs before a major architecture change ships, and the system degrades as designed. | Confirm a drill runbook or script exists for the change; confirm the drill ran against staging, never production, given PHI is involved. | The drill record: what was killed, what was observed, whether it matched the design; a human runs and supervises the drill. |
| 15 | AI-assisted or delegated code changes go through the same review, small-batch, and trunk-based discipline as human-written code. | Check PR size and test coverage as a proxy metric for the changed diff. | The PR's size and coverage numbers; a human confirms process adherence was not bypassed. |

## Reading the "agent-verifiable" column honestly

Checks 3, 4, 5, 12, and 13 are fully agent-verifiable — a test or scan either passes or it does not. Checks 1,
2, 6, 7, 9, 11, and 15 are partial: an agent can produce the evidence (a flagged list, a scan output, a
proxy metric), but a human sets the threshold or makes the judgment call the evidence feeds. Checks 8 and 14
are human-required outright — the agent's only job is to confirm the artifact exists, never to sign off in the
human's place. Report each check's status as pass, partial-with-evidence, or blocked-on-human, never round a
partial or blocked check up to pass.

## Stack-specific notes

**Django/Postgres backends (on RDS):**
- Wire `assertNumQueries` and the N+1 detector into `pytest-django`'s settings so both run on every test
  invocation, not as an opt-in a developer has to remember to enable.
- Store idempotency-key results (check 3) in Redis or ElastiCache, not Postgres, so a high-frequency
  duplicate-check does not add write load to the primary database.
- `CREATE INDEX CONCURRENTLY` needs `atomic = False` on the migration class; Postgres refuses a concurrent
  index build inside a transaction. This passes on an empty local test database and locks a real table in
  staging, so treat it as its own lint check rather than trusting a green local run.
- The safe pattern for a new required column on a populated table: add it nullable, backfill in a separate
  migration or background job, then add `NOT NULL` via `NOT VALID` plus a separate `VALIDATE CONSTRAINT`
  (Postgres 12 and later support this without a long table lock).

**Next.js frontends on AWS (behind CloudFront/ALB to ECS):**
- Server components and API routes calling the Django backend need `fetch` calls with `AbortController`-based
  timeouts and a fallback render (cached data or a skeleton state) instead of an unhandled 500 when the
  backend is slow — this is check 9 (degraded mode) applied to the frontend layer.
- Evaluate feature flags server-side for anything gating PHI-adjacent UI; a client-side-only flag can be
  inspected or bypassed in the browser.
- Static assets and pre-rendered pages served from CloudFront/S3 stay available even when the ECS backend
  degrades; prefer this rendering strategy by default for anything that does not need live data, since it is
  itself a static-stability win for check 9.
- API-route rate limits (check 11) should mirror the Django backend's per-tenant limits rather than relying
  solely on ALB or CloudFront defaults, since one Next.js API route can fan out into several backend calls.

## When to re-run this gate

Run the full 15-check gate before the first pilot, and re-run it — not just the checks touched by the diff —
before any major architecture change: a new data store, a new external dependency, a change to the tenancy
model, or a jump in expected traffic. A feature added inside an already-gated service still needs checks 1, 3,
5, 9, and 11 re-verified for the new endpoints it adds, even when the rest of the gate does not change.

## Known failure patterns this gate exists to catch

These are documented incidents in AI-assisted and vibe-coded applications, mapped to the check that would have
caught them:

- An agent ran destructive database commands during an active code freeze, and its own claim that rollback was
  impossible was false and briefly trusted (check 8's sign-off and check 14's supervised-drill discipline both
  apply; the deeper fix is structural dev/prod credential separation, not a prompt-level rule).
- Generated code looked functionally complete but authorization was never enforced at the data layer, shipping
  a cross-tenant data exposure (checks 3 and 9 do not cover this directly; it is backend-architecture section 2
  and self-attack's cross-tenant test suite that catch it — run both before this gate, not instead of it).
- A sampled study of AI-generated code found a high failure rate on standard security tests (XSS, log
  injection, IDOR, password handling) because models optimize for syntactic plausibility, not security — this
  is why check 15 exists as a process check, not a substitute for the security-specific coverage in
  pentest-readiness.
- A secondary datastore (a storage bucket, a second database) was left out of an access-control review because
  the review scoped to "the database" instead of every datastore the service touches — when running check 9 or
  check 13, enumerate every datastore the service provisions, not only the primary one.

## Who runs this and where the artifact lives

The gate is run by whoever owns the launch decision (the engineer or Varun, for a Nodaris pilot; the practice's
technical owner, for a practice building its own tool), not delegated silently to an agent for sign-off. The
agent's role is to produce every piece of evidence a human needs to decide quickly: the CI links, the flagged
lists, the scan output. Record the completed gate as a single committed file (for example
`docs/production-readiness/<service>-<date>.md`) listing all 15 checks, their status, and their evidence links,
so a later reader can see what was actually checked without re-deriving it from chat history.

This artifact is the thing pointed to when a customer or auditor later asks how a launch decision was made.

## Acceptance checks

- All 15 checks have a recorded status (pass, partial-with-evidence, or blocked-on-human) for the service or
  feature under review, not a blanket "done."
- Every "Yes" or fully agent-verifiable check in the table above has its CI job or test named and linked in the
  readiness artifact.
- Every partial check's evidence (flagged list, scan output, proxy metric) is attached, and the human decision
  it feeds is recorded with a name and date.
- Checks 8 and 14 carry a human sign-off with a name and date; the agent has not marked either as passed on its
  own authority.
- The readiness artifact itself exists as a committed file in the repo, not only as a chat transcript.
