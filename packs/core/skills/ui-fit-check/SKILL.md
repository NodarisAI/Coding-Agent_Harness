---
name: ui-fit-check
description: Use before calling any web UI change done, and in every UI builder's brief. Loads each changed route in a real browser at two widths and two zoom levels and fails on text that does not fit — spilling out of a table cell or card, cut to a few letters by an ellipsis, headers or buttons breaking onto two lines, overlapping text, sideways scroll — then makes you look at the screenshots. Built after NoRCON v3.1 was reported done while its tables showed "Blu…", "Deposi…" and a due date printed over the next column.
---

# UI fit check: nothing is done until it fits and someone has looked

A green test suite and a green overlap check both passed on screens a customer would call broken. The defects
that got through are specific, so the check is specific:

| Rule | Fails when | The case that got through |
|---|---|---|
| `cell` | text runs past the edge of its table cell | "Wed 14 Oct" printed over "55 · Incomplete" |
| `box` | text runs outside, or touches the edge of, its card or panel | a chart's last row hanging below its card |
| `trunc` | an ellipsis hides an id, a label of 16 characters or fewer, or more than half the text | "Blu…", "Deposi…", "CLM-26090…" |
| `wrap` | a column header, tab, chip or button label breaks onto a second line | "Date of / service", "Action / required" |
| `overlap` | two runs of text overlap by more than 2 px each way | stacked labels |
| `cut` | text is clipped with no ellipsis | a label sliced mid-letter |
| `hscroll` | the page or a table's box scrolls sideways | a table wider than its card at 1280 |
| `spill` | a fixed-height list (`[data-fit]`) holds more rows than it shows | rows under the pager |
| `small` | text under 10.5 screen px (use `--warn small` when the app runs at a reduced zoom on purpose) | |
| `error` | an uncaught page error | |

## Run it

```bash
node ~/ai-os/skills/ui-fit-check/scripts/fitcheck.cjs \
  --base http://localhost:5181 --routes routes.txt \
  --widths 1512,1280 --zooms 0.75,1 --warn small \
  --init '{"norcon-tenant":"anesthesia","norcon-theme":"light"}' \
  --shots /path/to/scratch/shots
```

- `routes.txt`: one path per line, `#` for comments. Add `>> <css selector>` to click something first (open a
  workspace, a tab, a drawer): `/v3/work/records >> tbody tr:first-child`.
- `--init` seeds localStorage before each load (tenant, theme, zoom). Run once per tenant or theme that matters.
- Exit 0 only when every page view is clean. The summary line is `N of M page views have findings`.
- In NoRCON the copy lives at `apps/web/scripts/fitcheck.cjs`: `FIT_ROUTES=<file> FIT_TENANT=cardiology pnpm -s check:fit`.
- Playwright is found from the project, then from the npx cache; Chromium from `~/Library/Caches/ms-playwright`.

## Then look

The script finds what can be measured. It cannot tell you a screen is crowded, misaligned, inconsistent with its
neighbours or ugly. Open the screenshots (`--shots`) with the Read tool, as crops of the part you changed, at both
widths, and fix what a person would notice. Say in the report which screenshots you looked at.

## Fixing what it finds (what worked)

- Merge a secondary column under a primary one (facility under the patient's name) before squeezing columns.
- Give fixed-format columns fixed widths (dates, money, ids) and let one text column take the rest.
- Let prose wrap to two lines (`line-clamp-2`) instead of cutting it to a few letters.
- Shorten a header ("Service date") rather than letting it wrap; never put `whitespace-normal` on a header.
- A card with a fixed height and a list inside needs `min-h-0` and its own scroll, or no fixed height.
- Mark a deliberate truncation (a long free-text note with a tooltip) with `data-truncate-ok`.

## In a builder's brief

Every agent that edits UI gets: the routes it owns, the exact command above for each tenant, the requirement to end
on `0 of N page views have findings`, and the requirement to read its own screenshots before reporting. An agent
that reports done without the fit-check totals and the screenshot paths has not finished.
