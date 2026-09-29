---
name: backend-architecture
description: Use when designing or reviewing a service or API for a multi-tenant healthcare backend — layering, tenancy enforcement, idempotency, background jobs, typed errors, migrations, pagination, observability, and API contracts. Assumes the tenancy, auth, and audit foundations from healthcare-app-blueprint already exist; this skill covers everything built on top of them. Pairs with self-attack for the adversarial tests that prove tenancy and idempotency actually hold.
---

# Backend architecture: services and APIs for a multi-tenant healthcare backend

A backend that answers requests correctly under a demo is not the same backend that survives a second tenant,
a retried webhook, a slow downstream payer API, and a migration on a table that now has real rows in it. This
skill is the set of decisions that hold up under those conditions.

## 1. Layering

Keep four layers distinct, even in a small service, so a rule enforced in one place cannot be bypassed by
calling another:

1. **View/serializer layer** — parses the request, enforces input shape and size, never contains business rules.
2. **Service layer** — the business rules and the only place a rule is written once; views call services, not
   the other way around.
3. **Data access layer** — a tenant-scoped manager or repository; the only code that constructs a queryset.
4. **Model layer** — the schema and its invariants (constraints, validators), not business logic.

A rule that lives only in a view (for example "a biller cannot see another practice's claims") gets bypassed
the first time a second view or a management command needs the same data. Put it in the data access layer so
every caller inherits it.

## 2. Tenancy enforcement at every layer

healthcare-app-blueprint covers building the tenant model and the scoped manager; this is the checklist for
every subsequent endpoint and background path that touches it:

- **Query layer:** every queryset that can return PHI-bearing rows is built from the tenant-scoped manager, not
  from the default manager with a `.filter(practice=...)` bolted on — a bolted-on filter is the one an agent or
  a future engineer forgets to add on the next endpoint.
- **Serializer layer:** tenant id is never read from the request body, query string, or header; it is derived
  from the authenticated principal's membership, every time.
- **Background jobs:** a Celery task or queue consumer that touches PHI carries the tenant id as an explicit,
  validated argument, and re-derives permission inside the task rather than trusting the caller that enqueued
  it — a task can be triggered by another task, a cron schedule, or a retry, not only by the original request.
- **Admin and internal tooling:** the Django admin, a management command, or an internal debug endpoint is
  either tenant-scoped the same way or restricted to an explicit superuser/staff role with its own audit trail;
  it is not a side door around the scoping used everywhere else.
- **Cross-tenant aggregation:** any legitimate cross-tenant read (billing, platform analytics) goes through a
  named, reviewed path that is obviously different from a normal request handler, never a general-purpose
  queryset with the tenant filter removed "just for this report."

## 3. Idempotency keys

Every state-changing endpoint, webhook handler, and queue consumer that can plausibly be retried — by a client
timeout retry, a webhook redelivery, or an at-least-once queue — must be idempotent:

- Accept an `Idempotency-Key` header (or derive an equivalent key from the payload, such as a payer's own
  transaction id) on every `POST`/`PUT`/`PATCH` that creates or changes billable or clinical state.
- Store the key, the tenant, and the result of the first successful execution in a fast store (Redis or
  ElastiCache, not the primary database, to avoid adding write load to Postgres for a high-frequency check),
  with a bounded TTL matching the caller's realistic retry window.
- On a replay of a known key, return the stored result without re-executing the side effect; do not re-run the
  handler and rely on a database unique constraint to silently absorb the duplicate — that still risks a
  partial side effect (an external API call, an email) firing twice.
- Queue consumers derive idempotency from the message's own id or content hash, since the queue itself may
  redeliver a message that was actually processed (the acknowledgment can be lost even when the work succeeded).

## 4. Background jobs and retries

- Every external call inside a job (payer API, LLM call, another internal service) has an explicit timeout and
  a bounded retry count with exponential backoff and jitter — an unbounded retry loop against a degraded
  downstream turns one outage into a queue backlog that outlives the outage.
  - `requests`/`httpx` calls: set `timeout=` explicitly; the library default is no timeout at all.
  - Celery tasks: set `max_retries`, a `retry_backoff` factor, and `retry_jitter=True`.
- Every queue has bounded consumer concurrency and a dead-letter queue (SQS `RedrivePolicy` or the Celery
  equivalent); a message that fails repeatedly moves to the DLQ instead of blocking or looping the main queue.
- A job that can cascade a downstream failure into the whole service (a payer API outage stalling every claim
  submission) is wrapped in a circuit breaker that fails fast once a failure threshold is hit, rather than
  letting every worker hang on the same slow dependency.
- Jobs are idempotent by design (see above) so a redrive from the DLQ after a fix is always safe to run.

## 5. Typed errors with reason codes

Return errors as a typed shape, not a free-text string, so a caller (the frontend, another service, or a
future integration) can branch on the error without parsing prose:

```json
{"error": {"code": "claim.duplicate_submission", "message": "This claim was already submitted.", "detail": {}}}
```

- Define the reason-code vocabulary once per domain (for example `claim.*`, `auth.*`, `tenant.*`) and keep it
  in one place that both the backend and any frontend error-mapping table can reference.
- The `message` field is safe to show a user; it never contains a stack trace, a raw exception string, an
  internal identifier, or a PHI value. The `detail` field is for machine-readable context (which field failed
  validation), not for restating PHI back to the caller.
- Map exceptions to reason codes at the boundary (a DRF exception handler, a middleware), not inline in every
  view, so a new endpoint inherits the mapping automatically.

## 6. Migrations: reversible and safe on non-empty tables

- Every migration has a working `reverse` path, or is explicitly marked irreversible with a recorded reason;
  an irreversible migration on a table that already holds tenant data needs a human sign-off before it runs
  against staging or production.
- Run migrations in CI through a zero-downtime-safe backend (for example `django-pg-zero-downtime-migrations`)
  so a blocking operation fails the build before it reaches a real database.
- Banned on a populated table without a two-step migration: `ALTER TABLE ... SET DEFAULT` combined with a
  backfill in the same migration, adding a `NOT NULL` column directly. The safe pattern: add the column
  nullable, backfill in a separate data migration or background job, then add `NOT NULL` via `NOT VALID` plus a
  separate `VALIDATE CONSTRAINT` (supported without a long table lock on Postgres 12 and later).
- `CREATE INDEX CONCURRENTLY` requires `atomic = False` on the Django migration class, since Postgres refuses a
  concurrent index build inside a transaction — a common silent failure that passes locally on an empty test
  database and locks a real table in staging.
- `makemigrations --check` runs in CI so a model change without a committed migration fails the build, not a
  future deploy.

## 7. Pagination and limits

- Every list endpoint is paginated by default (cursor pagination for anything that can grow past a few thousand
  rows, offset pagination only for small, bounded lists); an unpaginated list endpoint is a denial-of-service
  waiting for the first practice with real volume.
- Set an explicit max page size the client cannot override past (for example 100), and a max request body size,
  max upload size, and max list-field length on every serializer — never rely on the framework's defaults,
  which are often unbounded or too generous for a public endpoint.
- Every public and partner-facing endpoint has a per-tenant rate limit with a defined `429` response and a
  `Retry-After` header; size the threshold from the endpoint's realistic legitimate traffic, not a guess.

## 8. Observability without PHI

- Structured logs go to stdout as JSON events with a request id and tenant id, never a name, date of birth,
  member id, or free-text clinical note; apply a logging filter that drops or masks identifier fields at the
  point logs are emitted, not as a downstream cleanup step.
- Trace external and model calls (HTTP, DB, queue, LLM) with OpenTelemetry's GenAI semantic conventions where
  applicable (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.response.finish_reasons`) so a
  production incident in an AI-assisted feature has a structured record of what was called and returned, not
  just a log line.
- Metrics (latency, error rate, queue depth) are tagged by tenant only when the tag itself cannot be reversed
  into an identifying value at low cardinality; avoid a metric dimension that is effectively a patient count
  for a single-patient tenant.
- Errors returned to a client are generic (see typed errors above); the detailed exception goes to the error
  tracker with PHI scrubbed from the request body and breadcrumbs before it leaves the process.

## 9. API contracts: OpenAPI and versioning

- Generate an OpenAPI schema from the code (`drf-spectacular` or equivalent) rather than hand-maintaining a
  separate document that drifts from the implementation; treat schema generation as a CI check, not optional.
- Version the API in the URL path (`/v1/...`) or a header, not by silently changing a response shape; a field
  rename or removal is a new version, additive fields are not.
- A breaking change (removed field, changed type, changed required-ness) needs a deprecation window with both
  versions served, and a recorded date the old version stops being served.
- Contract tests (or a schema-diff check in CI) catch a response that no longer matches the published schema
  before a frontend or partner integration breaks silently.

## Acceptance checks

- A cross-tenant test proves every new endpoint returns 403 or 404 for a user of another tenant, on list, read,
  update, and delete.
- Every new state-changing endpoint and queue consumer has a test asserting a duplicate delivery produces one
  effect, not two.
- Every new external call (HTTP, LLM, queue) has an explicit timeout and a bounded retry with backoff, visible
  in the code, not left to a library default.
- `makemigrations --check` passes, the migration runs clean through the zero-downtime backend, and any
  irreversible migration is named and justified.
- Every new list endpoint is paginated with a bounded max page size; every new endpoint has a documented
  request-size limit.
- A log or trace captured from exercising the new code path contains a request id and tenant id and no PHI
  field, verified by a test that asserts on the captured log content.
- The OpenAPI schema regenerates without a manual diff against the implementation, and any breaking change to
  an existing field is called out with its deprecation plan.
- Every typed error returned by the new code uses the shared reason-code vocabulary and carries no stack trace
  or PHI in its user-facing message.
