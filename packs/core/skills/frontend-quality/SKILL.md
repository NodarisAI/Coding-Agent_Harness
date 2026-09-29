---
name: frontend-quality
description: Use when building or reviewing production UI for a healthcare application — patient portals, biller dashboards, intake forms, claim or denial screens. Covers accessibility (WCAG 2.2 AA), performance budgets, state and data fetching, forms and validation, error and empty states, keeping PHI off the screen and out of client-side storage, design-system reuse, and the testing layers a UI change needs before it ships. Pairs with healthcare-app-blueprint (the backend foundations) and self-attack (adversarial testing of the surface this skill builds).
---

# Frontend quality: production UI for a healthcare app

A screen that "looks done" and a screen that is production-ready are different things. This skill lists what
separates them for healthcare UI specifically, where the extra failure mode is patient data leaking through a
channel nobody thought of — a URL, a browser cache, an analytics event, a screenshot in a bug report.

## 1. Accessibility: WCAG 2.2 AA, with the exact numbers

Build to these criteria; do not treat accessibility as a pass at the end.

| Criterion | Level | Requirement |
|---|---|---|
| 1.4.3 Contrast (Minimum) | AA | Normal text at least 4.5:1; large text (18pt or larger, or 14pt bold) at least 3:1 |
| 1.4.11 Non-text Contrast | AA | UI components, borders, focus indicators, meaningful graphics at least 3:1 |
| 2.4.7 Focus Visible | AA | A visible focus indicator on every keyboard-focusable element |
| 2.4.11 Focus Not Obscured (Minimum) | AA, new in 2.2 | A focused element must not be fully hidden by a sticky header, banner, or cookie notice |
| 2.5.8 Target Size (Minimum) | AA, new in 2.2 | Pointer targets at least 24x24 CSS px, with five named exceptions (inline text links, for example) |
| 2.4.1 Bypass Blocks | A | A skip link to the main content |
| 2.1.2 No Keyboard Trap | A | Every component that can be entered with the keyboard can be exited with the keyboard |
| 1.1.1 Non-text Content | A | Every image has alt text; decorative images have empty alt (`alt=""`), never a missing attribute |
| 1.3.1 Info and Relationships | A | Semantic landmarks and heading structure, not divs styled to look like headings |
| 3.3.2 Labels or Instructions | A | Every form field has a real `<label>`, never a placeholder standing in for one |
| 2.3.1 Three Flashes or Below | A | Nothing flashes more than three times per second |

Build interactive targets at 44x44 CSS px as the working default; treat 24px as the regulatory floor, not the
target. `prefers-reduced-motion` is an AAA criterion (2.3.3) but costs little to honor — respect it for any
animation longer than a subtle transition.

The regulatory driver for US healthcare vendors: an HHS Office for Civil Rights interim final rule requires
covered entities and their software vendors to meet WCAG 2.1 AA by 2027-05-11 for organizations with 15 or more
employees (2028-05-10 for smaller ones), under 45 CFR Part 84. Any customer-facing surface should be built to
this bar now, not audited into compliance later.

Automated tools (axe-core, pa11y, Lighthouse) catch roughly half of real WCAG issues by axe-core's own
documentation. Run `@axe-core/playwright` assertions inside signed-in e2e journeys as a floor, then do a manual
keyboard pass: tab through the whole screen, confirm focus is always visible, confirm nothing traps it, confirm
every action reachable by mouse is reachable by keyboard.

## 2. Performance budgets

Set numeric budgets per route and fail the build or the e2e run when a page misses them, measured against the
Core Web Vitals thresholds:

| Metric | Good | Needs improvement | Poor |
|---|---|---|---|
| Largest Contentful Paint (LCP) | under 2.5s | 2.5s to 4.0s | over 4.0s |
| Interaction to Next Paint (INP) | under 200ms | 200ms to 500ms | over 500ms |
| Cumulative Layout Shift (CLS) | under 0.1 | 0.1 to 0.25 | over 0.25 |

Measure on the 75th percentile of real users where analytics exist, and in CI with Lighthouse against a
throttled profile (4x CPU slowdown, simulated 3G-fast) for a repeatable regression signal. Set a JS bundle
budget per route (for example 200KB gzipped for an initial route chunk) and fail CI when a change exceeds it
without an explicit budget-increase decision recorded.

## 3. State and data fetching

- Server state (anything that comes from the API) and client state (form drafts, UI toggles) are different
  concerns; do not put server state in a global client store that can drift from the backend.
- Use a fetching layer with built-in caching, retry, and stale-while-revalidate semantics (React Query, SWR, or
  the framework's own server-component data layer) rather than ad hoc `useEffect` fetches — an ad hoc fetch
  cannot cancel on unmount and often causes a duplicate PHI request on every re-render.
- Every fetch that crosses the network gets an explicit timeout via `AbortController` and a defined behavior on
  failure: a retry with backoff for idempotent reads, a typed error state for everything else. An unhandled
  fetch rejection must not crash the page to a blank white screen.
- Optimistic updates are reverted on failure with the exact previous value restored, and the user is told the
  action did not take effect.

## 4. Forms and validation

- Validate on the client for immediate feedback and on the server as the source of truth; never trust the
  client's validation as the only gate, since the API can be called directly.
- Every required field states that it is required in text, not color alone. Every error message names the
  field and the fix ("date of birth must be in the past"), never a bare "invalid input."
- Disable the submit button only after showing the pending state, and re-enable it on failure — a permanently
  disabled button after a failed submit is a support ticket.
- Preserve entered data across a validation error and across a network failure; nobody should have to retype a
  20-field intake form because one field failed.

## 5. Error and empty states

Every screen that can show data needs four states designed and tested, not just the happy path: loading, empty
(zero results, distinct from "loading"), error (with a retry action, not just a message), and populated. A list
screen with no empty-state design will ship as a blank page the first time a new practice has no data yet.

## 6. PHI must never reach a channel that was not built to hold it

- **URLs and query strings:** never put a patient name, date of birth, member id, or claim identifier in a URL
  path or query parameter — URLs land in browser history, server access logs, and analytics referrer data.
  Use an opaque internal id in the URL and fetch the PHI-bearing fields by an authenticated call.
- **Client-side analytics:** configure the analytics SDK to strip request/response bodies and to never send DOM
  text or form values as event properties; review every custom event for a PHI field before it ships.
- **Local storage, session storage, IndexedDB:** do not cache PHI in any browser storage. If a draft must
  survive a refresh, store a non-PHI reference and refetch, or store nothing and accept the redo cost.
- **Client-side logs and error reporting:** configure the error reporter (Sentry or equivalent) to scrub request
  bodies, form values, and breadcrumb text; test that a thrown error inside a component holding a patient
  object does not serialize that object into the report.
- **On-screen masking:** mask identifiers that do not need to be fully visible (show the last four digits of a
  member id, for example) and require an explicit action to reveal the rest, logged the same way a backend read
  is audited under healthcare-app-blueprint's audit-log foundation.
- **Screenshots and screen recordings:** any support-tooling screen recorder (session replay tools) must be
  configured to block or mask PHI-bearing DOM regions before it is enabled on a signed-in surface.

## 7. Design-system reuse

Route to the project's own design tokens and component library before writing a new component; a component
existing in the library does not guarantee an agent finds and prefers it, so state the routing explicitly in
the project's `CLAUDE.md` or `AGENTS.md` (for example, "check `docs/UI-BRIEF.md` and the component index before
adding a new component"). Do not fork a design-system component to fix one screen's spacing; fix the token or
the component, since the fork is what causes drift.

## 8. Testing layers

| Layer | Tool | What it proves |
|---|---|---|
| Unit | Vitest or Jest | A function or hook's logic in isolation |
| Component | Testing Library (React/Vue) | A component renders correctly for a given prop set and responds to user events |
| End-to-end | Playwright | A full user journey through the running app, behind authentication for signed-in surfaces |
| Visual regression | `toHaveScreenshot` (Playwright) | Pixel-level layout regressions a functional test cannot catch |
| Fit | `ui-fit-check` skill (`scripts/fitcheck.cjs`) | No text spills out of a cell or card, is cut to a few letters, wraps in a header or button, or overlaps, at two widths and two zoom levels |

Visual baselines are committed and reviewed like code, never silently regenerated to make a failing test pass;
dynamic regions (timestamps, generated ids) are masked in the screenshot, not disabled as a test. Animations
are turned off globally in the test config. When a screenshot diff fails, read the diff image before proposing
a fix — it usually states the cause directly (a component is 4px taller because of new padding).

## Acceptance checks

- Every changed route passes the `ui-fit-check` script (two widths, the default zoom and 100%, each tenant or theme)
  with `0 of N page views have findings`, and the screenshots were opened and looked at before the change was
  called done.
- Every page in the diff passes an automated axe-core scan with zero violations at the AA ruleset.
- A manual keyboard pass reaches every interactive element on the changed screens; focus is always visible.
- LCP, INP, and CLS are measured for each changed route and meet the "good" thresholds above, or the budget
  exception is recorded with a reason.
- Every network call added in the diff has a timeout and a defined failure-state render.
- Every new or changed form preserves entered data across a validation error and shows a field-specific message.
- Loading, empty, error, and populated states exist and are covered by a component or e2e test for every screen
  that renders a list or detail view.
- A search of the diff for PHI-shaped identifiers in URL construction, `localStorage`/`sessionStorage` calls,
  and analytics event payloads returns nothing.
- New components resolve to the existing design-system tokens and library; a new one-off component is
  justified in the PR description if the library has no fit.
- Unit, component, and e2e suites for the changed area pass; any new visual baseline is reviewed, not
  auto-accepted.
- The harness's copy check and design check came back with nothing after the last edit to each changed UI file,
  or each remaining item carries a `design-lint-ignore: <reason>` the reviewer accepted. Visible text follows the
  `plain-copy` skill; surfaces are solid, colours come from the design system, and motion is short and ease-out.
