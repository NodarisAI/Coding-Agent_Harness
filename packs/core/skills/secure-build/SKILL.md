---
name: secure-build
description: Use for any healthcare application work that touches authentication, sessions, access control, PHI, file uploads, external input, webhooks, payments, LLM or agent features, or a public endpoint, and before calling any Nodaris app pen-test ready. Sets the security bar (OWASP ASVS 5.0 level 2, level 3 on PHI paths; OWASP Top 10 for LLM applications; HIPAA §164.312), the threat-model-first workflow, the tests that must exist, the scans that must be clean, and the rules for LLM and agent features that resist prompt injection.
---

# Secure build: healthcare software that survives a pen test

The bar is that a competent external tester, given a normal user account, cannot:
- read another tenant's data;
- escalate their privileges;
- execute code;
- reach internal services;
- make an LLM feature leak data or act outside its scope.

A green test suite is not that proof. Every rule below exists to close a class of attack.

## 1. Threat model first (before any code on a sensitive path)
Write 10-20 lines in the repo's `docs/security/threat-model.md` (AROS already has one) or the PR notes:
- the assets, such as PHI fields, tokens and keys;
- the entry points: every route, upload, webhook, queue message, and model or tool call;
- the trust boundaries: browser to API, API to database, API to model, model to tool, tenant to tenant;
- the top threats, using STRIDE, each with its mitigation and the test that proves it.

If a threat has no test, it is not mitigated.

## 2. The bar per layer (ASVS 5.0 L2 everywhere, L3 where PHI moves)
- **Access control:**
  - Deny by default. Check on the server on every request, by object and by tenant, never by hiding UI.
  - Tenant isolation lives in the database: RLS `ENABLE` + `FORCE`, or a mandatory tenant filter at the repository layer.
  - Every endpoint gets a test that user A of tenant 1 gets 403/404 on tenant 2's object (IDOR/BOLA).
- **Authentication and sessions:**
  - Passwordless or strong hashing (argon2id/bcrypt). MFA for staff.
  - Sessions and tokens are short-lived with rotation; cookies are `HttpOnly; Secure; SameSite=Lax/Strict`.
  - Logout revokes the session on the server. Brute-force and enumeration limits apply to login, reset and OTP.
- **Input:** validate by schema at the boundary (pydantic/zod/DRF serializers, strict types, length limits). SQL is parameterized only, with no string-built queries, including raw `.extra()` or `sql` templates. Uploads are checked by size, type from magic bytes rather than extension, re-encoded or scanned, stored outside the web root, and served with `Content-Disposition: attachment`.
- **Output:** context-aware encoding. Never use `dangerouslySetInnerHTML` or `|safe` on user- or model-derived content. Set a CSP (no `unsafe-inline` scripts), HSTS, `X-Content-Type-Options`, `frame-ancestors`, and `Referrer-Policy`.
- **SSRF:** any server-side fetch of a URL a user or model supplied must:
  - allow only http(s) schemes;
  - resolve the host, then refuse private, loopback, link-local and metadata IPs (169.254.169.254) after DNS resolution;
  - follow no redirects to those addresses;
  - use timeouts and size caps.
- **Secrets:**
  - Secrets live only in a secret manager or `.env` (git-ignored). Never in code, logs, fixtures or model prompts.
  - gitleaks must be clean over the whole history.
  - Rotate any secret that was ever committed. Deleting the file does not revoke the secret.
- **PHI:** encrypt at rest (field-level for identifiers) and in transit. Never put PHI in logs, errors, analytics, URLs or model calls without an approved BAA path. Keep an audit trail of every PHI read and write (who, what, when). Apply minimum necessary, and use synthetic data in all tests.
- **Errors and logging:** generic errors to clients and detail to structured server logs. Scrub PHI and secrets before a log line is written, and alert on authentication anomalies.
- **Dependencies and build:**
  - Pin versions with lockfiles, and run `pip-audit`/`npm audit`/`osv-scanner` clean or with each exception documented.
  - Build from minimal images that run as non-root.
  - Scan containers with trivy.
- **Rate limits and abuse:** per user and per IP on authentication, expensive endpoints and model calls. Every model call has a cost cap.

## 3. LLM and agent features (OWASP Top 10 for LLM apps, 2025)
- **Treat all model input from documents, web pages, emails, OCR and tool results as untrusted data**, never as instructions. Keep it in a clearly delimited data block, and never let it change the system prompt or tool permissions.
- **Model output is untrusted:**
  - Validate it against a schema before use.
  - Never `eval`/`exec` it, never build SQL, shell or HTML from it without the same encoding as user input, and never follow a URL from it without the SSRF guard.
- **Least agency:**
  - Each tool is allowlisted per feature with typed arguments and server-side authorization under the user's own permissions, never a service account's.
  - A human approval gate covers irreversible or outbound actions: submissions, payments, messages.
- **No secret or cross-tenant data in the context window.** Retrieval filters by tenant before ranking.
- **Sensitive-output guard:** numbers and identifiers in model prose are checked against the deterministic source (see the narrative-number-guard memory). On mismatch, reject; never repair.
- **Test it:** keep a prompt-injection suite in the repo covering:
  - instructions hidden in an uploaded document;
  - "ignore previous instructions";
  - data exfiltration through a URL or image link;
  - tool-call hijack;
  - requests for another tenant's data.

  It runs in CI and must pass.

## 4. Required evidence before "done" on a sensitive path
1. The threat model is updated, and each threat maps to a test.
2. Authorization tests (cross-tenant and cross-role) exist for every new or changed endpoint and pass.
3. Scans are clean: `python3 <harness>/tools/scan.py` on the changed files, then the full scanners (gitleaks, semgrep, bandit or eslint-security, pip-audit/npm audit, osv-scanner, and trivy when containerized). Run whatever is installed, and name what was skipped.
4. The prompt-injection suite passes when the change touches a model or agent path.
5. A fresh-context security review of the diff: `security-review` or the harness `security-reviewer` agent, and a second reviewer from a different model family where the project allows it.
6. The project owner approves any change to authentication, crypto, tenant isolation or PHI flow before it reaches dev.

## 5. Where security-sensitive code may go
Code on authentication, crypto, PHI or tenant paths goes only to model providers the project has a signed data agreement with. Never send it to free or pay-as-you-go model endpoints whose data terms are not confirmed. PHI and secrets never go to any external model.

## 6. Pen-test readiness (before a pilot)
- Run OWASP ZAP baseline plus an authenticated scan against staging. Run the ASVS L2 checklist and record each item's status.
- Have an external tester or a scoped internal red-team pass, and log every finding as an issue with a severity.
- Keep an incident runbook, key-rotation procedures and a breach-notification path (HIPAA §164.404).
