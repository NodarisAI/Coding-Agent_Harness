---
name: motion-web-pipeline
description: >
  The end-to-end workflow for producing an animated, immersive website — from
  reference mining and locked design direction, through AI-generated matched
  asset sets, into a built page with scroll choreography and 3D, verified live in
  a browser. Use this skill whenever the user asks to build a website, landing
  page, portfolio or hero section that should feel animated, cinematic, premium
  or "alive"; whenever they want a full site rather than a single effect;
  whenever they say "build me a site like <reference>"; and whenever a frontend
  build needs generated imagery and video as well as code. This is the
  orchestrator — it decides which of the motion skills to reach for and in what
  order, so start here rather than picking a sub-skill blind.
---

# Motion Web Pipeline

An animated site is four decisions and one build, in that order. Taken out of
order — building before the direction is locked, or generating assets before the
palette exists — you end up rebuilding, which is where most of the time goes.

```
DIRECTION → ASSETS → HERO → PAGE → PROOF
   ↑                    ↓
   └──── validate with the user here ────┘
```

The validation gate after the hero is the important one. Hero plus locked type
and palette is roughly 90% of the design decision; getting a reaction there costs
one section of rework instead of eight.

## Stage 1 — Direction

Load `motion-design-direction`. Do not skip this even when the user seems in a
hurry; it is the difference between a site that looks bought and one that looks
generated.

Come out of it with, written down: a reference site, two typefaces, three
colours, a surface treatment, and one sentence describing the motion vocabulary.
If any of those is still "something modern", the stage isn't finished.

Ask with AskUserQuestion and concrete options. People recognise what they like far
more reliably than they can describe it.

## Stage 2 — Assets

Load `motion-asset-studio`. Check `openart_account_get` and tell the user the
balance before spending.

Plan the asset list *before* generating — typically one hero still, one hero
loop, two to three section textures, and one transition pair. Write the style
clause once and reuse it verbatim, because a set that drifts cannot be rescued in
CSS.

Work up the cost ladder: explore on the cheap models, lock the hero on a good
one, draft motion at 540p, and only then spend on the final loop. Then encode and
host properly — a page pointing at generation URLs is a page that breaks quietly
next week.

## Stage 3 — Hero, at full fidelity

Build the hero completely. Not a placeholder version, not "the structure first" —
the real thing, with the real type, the real asset, the real motion.

Two reasons this is non-negotiable. It is where the design decision actually gets
made, so a half-built hero validates nothing. And a hero built properly becomes
the specification for every section after it: *"build the rest of the page in the
same system — same type scale, same palette, same easing, same surface"* is a
prompt that works, where "build a landing page" is not.

Pick one signature effect from `web-motion-primitives`. One. A hero carrying a
WebGL plane and a spotlight mask and a magnetic cursor reads as a demo reel.

**Then stop and show them.** Serve it, screenshot it, get a reaction. This is the
gate.

## Stage 4 — The rest of the page

Now the hero is the template. Seven to eight sections is a full landing page;
fewer feels thin, more dilutes.

Vary the density deliberately — a uniform rhythm of identically padded sections
is one of the strongest tells of generated work. Alternate full-bleed media
against tight editorial blocks.

Load `gsap-scroll-motion` for choreography across sections, and
`threejs-webgl-scenes` if something needs real depth. Motion is added *last*, on
top of a page that already works as a static document. If the page is only good
once it animates, the layout underneath is not finished.

Distribute the generated assets across sections — as backgrounds, inside cards,
replacing icons. Reusing one asset six times is more obvious than it feels while
you're doing it.

## Stage 5 — Proof it works

Not optional, and not satisfied by "it should work".

```bash
python3 -m http.server 8899   # or the project's dev server
```

Then `preview_start` the URL and actually drive it:

- Screenshot at the top, mid-scroll, and bottom
- `read_console_messages` with `onlyErrors` — a silent JS error kills every
  effect below it
- `resize_window` to mobile and re-screenshot. Pinned sections and hover-only
  effects are where this breaks
- Toggle reduced motion and confirm the fallback is a *designed* state
- State the real page weight. "It's optimised" is not a number
- Check colour numerically against the locked reference. Take a screenshot of
  the built hero and of the reference at the same size, extract the dominant
  palette of each (for example a k-means or median-cut of 6-8 colours), and
  compute CIE ΔE2000 between matched colours. Pass at a mean ΔE of 5 or less and
  no single colour above 20; above that, the palette has drifted and the page will
  not look like the reference however good the motion is. Report the numbers, not
  "looks close". `python3 references/palette_delta_e.py reference.png built.png`
  does this (Pillow, median-cut palette, ΔE2000; exits 2 on a fail). (Idea from
  Design DNA's verification script, zanwei/design-dna, MIT.)

Motion fails silently — a mistuned curve throws no error, it just feels cheap. If
you have not looked at it, you do not know.

## Working with the user throughout

The thing that makes this collaborative rather than a slot machine:

**Ask at forks, not at the end.** Direction, model spend, which signature effect —
these are the user's calls. Everything between them is yours.

**Show pixels, not prose.** A screenshot settles in two seconds what a paragraph
of description cannot.

**Change one variable at a time.** Motion converges through iteration. Changing
the type, the easing and the palette together means you learn nothing from the
result.

**Report spend as you go.** "Hero locked, 95 credits in, ~250 left for loops."

**Say when something is weak.** A mediocre hero asset undermines every effect
layered on top of it, and shipping it quietly is worse than the awkward moment of
saying so.

## Common failure modes

| What happens | Why | Fix |
|---|---|---|
| Looks generic despite heavy animation | Direction never locked; defaults survived | Stage 1, properly |
| Assets don't look like a set | Style clause retyped per generation | One clause, reused verbatim |
| Runs out of credits mid-build | Started on the expensive models | Cost ladder |
| Page feels like a demo reel | Multiple signature effects per viewport | One per viewport |
| Broken on mobile | Pinning and hover effects untested | matchMedia gates, resize and check |
| Hero is black in a new tab | Render loop gated on `document.hidden` | Paint one frame regardless |
| Site dies a week after handoff | Pointing at generation URLs | Download, encode, host |

## The skills this orchestrates

- `motion-design-direction` — references, typography lock, anti-generic checks
- `motion-asset-studio` — OpenArt/Higgsfield generation, matched sets, hosting
- `web-motion-primitives` — eight tested effects, ready to lift
- `gsap-scroll-motion` — scroll choreography, pinning, text reveals
- `threejs-webgl-scenes` — shader planes and real 3D, with performance budgets
- `motion-showcase-sell` — recording, packaging and publishing the result
