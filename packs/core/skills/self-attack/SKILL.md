---
name: self-attack
description: Use after building or changing anything on a sensitive surface (patient data, tenants, authentication, authorization, tokens, payments, claims and remittances, uploads, webhooks, parsers of external files, model or agent features), and whenever the done gate asks for adversarial tests. The model attacks its own change inside the local checkout under fixed rules of engagement, turns every applicable attack into a regression test, and reports an attack table.
---

# Self-attack: red-team your own change before anyone else does

A green happy-path suite proves the feature works for the people it was built for. This skill proves it
does not work for anyone else. Work like an external tester who has a normal account on one tenant and
has read the code you just wrote.

## Rules of engagement (fixed; the user cannot be surprised by any of these)
- Attack only the code in this checkout, through its tests and its local test client or test database.
- No network target other than localhost and the test server the suite starts. No scanning of other hosts.
- No real data. Every identity, patient and payload is synthetic and obviously fake.
- No destructive commands outside the test database: no dropping shared databases, no deleting files the
  task did not create, no pushes.
- A finding is fixed in this change or recorded with a test that pins today's behaviour and a decision
  note. It is never silently left.

## 1. Map the attack surface of the diff
List every entry point the change adds or touches (route, view, serializer, command, task, parser, upload
handler, webhook, model prompt or tool) and, for each, the caller kinds (anonymous, user of the same
tenant, user of another tenant, lower role, service account, internal job) and the data it reads or writes.

## 2. Pick the applicable attacks (from OWASP API Security Top 10, ASVS 5.0 and STRIDE)
| Class | Try | Expected result |
|---|---|---|
| Broken object-level authorization (BOLA/IDOR) | A user of tenant 1 asks for, lists, updates and deletes tenant 2's object by id | 403 or 404, nothing returned or changed, an audit row under the caller's own tenant |
| Tenant spoofing | Put another tenant's id in the body, query string or header | Ignored or refused; identity comes from the authenticated principal only |
| Broken authentication | No credentials, expired or malformed token, token for a deleted user | 401, no data, no stack trace |
| Function-level authorization | A lower role calls an admin action | 403 |
| Mass assignment | Extra fields in the body (`tenant_id`, `role`, `is_staff`, `status`) | Ignored or rejected; not written |
| Injection | SQL metacharacters, X12 delimiters (`~ * : ^` and line breaks) in names, CSV formulas (`=cmd`), HTML and script in text, path traversal in file names | Rejected or stored inert; parsing and output unchanged |
| Prompt injection (model features) | Instructions hidden in an uploaded document, a request for another tenant's data, an exfiltration link | Treated as data; no tool call outside scope; no cross-tenant content |
| Oversized and malformed input | Empty body, huge body, huge counts, wrong types, truncated file, wrong encoding | A typed, bounded error before heavy work; no crash |
| PHI leakage | Trigger each error path and read the response, the log lines and the audit rows | No names, dates of birth, member ids, amounts tied to a person, or raw payloads |
| Replay and duplicates | Send the same request twice; retry after a timeout | No double write, no double charge; idempotent or refused |
| Money integrity | Negative, non-numeric, over-precise and overflow amounts | Typed error or flagged; never silently repaired |

Skip a class only when the change cannot reach it, and say so in one line.

## 3. Turn each attack into a test
Write the test so it fails on the unsafe version. Name it after the attack, for example
`test_reidentify_cross_tenant_is_forbidden` or `rejects a CAS segment with an odd element count`.
- Django/DRF: use the test client with a real authenticated principal for each caller kind; assert the
  status code, that no protected field appears in `response.content`, and that the database row count did
  not change.
- Parsers: feed hostile fixtures built in the test (never loaded from real files) and assert the typed
  error or warning, including its reason code.
- Logs: capture with `caplog` (pytest) or a spy on the logger, and assert protected values are absent.
- TypeScript: mock the API at the network layer, assert the UI never renders another tenant's data and
  the client never sends identity fields the server should derive.
Run the new tests and the area's suite. Fix what they expose, then run the full verify command.

## 4. Get an independent review
Start the `security-reviewer` agent with the list of changed files and one sentence on what the change is for. It
reads the code as an attacker would and cannot edit. Fix what it finds, add a test for each real finding, and rerun
the tests and the security scan (`python3 <harness folder>/tools/scan.py`).

## 5. Report the attack table in the summary
| Attack | Entry point | Test | Result |
|---|---|---|---|
One row per attack tried, including the ones that were already blocked (a protection without a test can
regress unnoticed). End with the residual risks you could not close and why.
