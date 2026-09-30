---
name: healthcare-app-blueprint
description: Use when someone wants a new application that will hold patient, claim, billing or practice data ("build me an app for my practice to track denials", "a patient intake portal", "a tool for our billers"), or when an early-stage app is missing its foundations. Gives an opinionated, production-grade reference architecture for a multi-practice healthcare application, the order to build it in, and the acceptance checks that prove each foundation works before any feature is added.
---

# Healthcare app blueprint: the foundations before the first feature

People who ask for "an app for my practice" describe the feature. The foundations they do not mention decide
whether it can ever hold real patient data: who can see what, what is recorded, what is encrypted, and how anyone
knows it works. Build those first, prove each one with a test, then build the feature on top.

## 1. Decide the shape (record each decision in `docs/decisions.md`)
- **Default stack** (reuse an existing one if the person already has it): Python 3.12, Django 5 with Django REST
  Framework, PostgreSQL 16, a React and TypeScript front end built with Vite, pytest and Vitest. It is boring,
  well documented and has mature answers for every item below.
- **Tenancy:** every practice is a tenant from day one, even if there is only one today. Adding tenancy later
  means auditing every query.
- **Where it runs:** a HIPAA-eligible service under a signed BAA (AWS, Google Cloud or Azure with the BAA
  accepted). Not a hobby host. State this in the decisions file; the code cannot enforce it.

## 2. Foundations, in this order, each with its proof
| # | Foundation | Build | Proof (a test that fails without it) |
|---|---|---|---|
| 1 | Verify script | `scripts/verify.sh`: format check, lint (ruff, eslint), types (mypy, tsc), tests, `makemigrations --check`, exit non-zero on any failure | the script runs green on the empty project |
| 2 | Settings hardening | secrets from environment only; `DEBUG` off by default; HTTPS-only cookies (`Secure`, `HttpOnly`, `SameSite=Lax`); HSTS; `X-Content-Type-Options`; CSP without `unsafe-inline` scripts; allowed hosts explicit | a settings test asserts each value in the production settings module |
| 3 | Tenant model and scoping | `Practice` (tenant) and `Membership(user, practice, role)`; a `TenantScopedManager` or base queryset that requires a practice; every model holding PHI has a non-null practice foreign key; DRF `get_queryset` filters by `request.user`'s membership, never by a request parameter | a test with users of two practices proves list, read, update and delete of the other practice's objects return 404 |
| 4 | Authentication | Django auth with argon2 password hashing, MFA (TOTP) required for every staff role, login rate limiting, session expiry after inactivity, logout that ends the server session | tests for lockout after repeated failures, MFA required before any PHI endpoint, expired session refused |
| 5 | Roles and object permissions | a small fixed set of roles (for example owner, biller, front desk, read only); DRF permission classes per view; deny by default | a test per role and endpoint pair that should be refused |
| 6 | Audit log | an append-only `AuditEvent(practice, actor, action, object_type, object_id, count, at)` written for every read and write of PHI; no PHI values in it; no update or delete path | tests that reading a patient writes one event, and that the event carries no name or date of birth |
| 7 | PHI at rest | field-level encryption for direct identifiers (names, date of birth, SSN, member ids) with keys from the cloud key manager; database encryption at rest on; lookup by a keyed hash when search is needed | a test that the stored column is not the plaintext value |
| 8 | PHI-safe logging | a logging filter that drops or masks identifier fields; structured logs with request ids; errors to clients are generic | a test with `caplog` that an error on a patient request logs no identifier |
| 9 | Input boundaries | serializers with explicit fields (never `__all__`), max lengths, max list sizes, max upload size, file type from content not extension | tests for oversized, malformed and extra-field requests |
| 10 | Synthetic data | a seed command that builds obviously synthetic practices, users and patients, refuses to run in production settings, and is idempotent | a test that it refuses under production settings |
| 11 | Threat model | `docs/threat-model.md`: assets, entry points, trust boundaries, top threats with the test that covers each | every threat names a test that exists |
| 12 | CI | a workflow that runs `scripts/verify.sh` and the security scan on every push; dependency lockfiles committed | the workflow file exists and the scan is clean |

## 3. Then the feature
Build the feature the person asked for on top of these, test-first, one vertical slice at a time. Every new
endpoint inherits tenant scoping, permissions and audit; add its cross-tenant test the same day.

## 4. Domain pieces that come up in practices
- **Claims and remittances:** follow the healthcare-domain skill for X12 837 and 835 (group and reason code pairs,
  delimiter safety, Decimal money). Parse external files in a worker, never in the request.
- **Eligibility (270/271) and clearinghouses:** credentials in the secret manager; every outbound call logged with
  the payer and outcome, never the member's details; timeouts and retries with idempotency keys.
- **Documents and faxes:** stored outside the web root, virus scanned, served with `Content-Disposition:
  attachment`, access audited.
- **Model features:** only through a vendor with a BAA; document text is data, never instructions; outputs that
  carry numbers are checked against the source.

## 5. Handing it over
The summary for a non-engineer lists which foundations exist and which proof passed, what still needs a person
(the BAA, the cloud account, backups and restore test, a penetration test before real patients), and how to run
`scripts/verify.sh`. Offer the trust receipt from the harness tools folder as the evidence.
