---
name: motion-design-direction
description: >
  Set the aesthetic direction for a website before building it — mine references
  from free galleries and component registries, lock typography and palette into
  tokens, and apply the checks that stop a page reading as generic AI output. Use
  this skill whenever the user wants a website or landing page that looks
  distinctive, premium, "not like AI slop", or "like a real agency built it";
  whenever they ask where to find design inspiration, free templates, free
  animated components, hero prompts, or reference sites; whenever they mention
  motionsites.ai, 21st.dev, Dribbble, Pinterest, Awwwards, Godly, Aceternity,
  Magic UI, React Bits or shadcn; and whenever a build is about to start and
  nobody has decided what it should look like. Also use it when the user says an
  existing design feels generic, flat, templated, or "off" but cannot say why.
---

# Motion Design Direction

The single largest quality difference between an AI-built site and an agency-built
site is decided before any code exists. Agencies arrive with a reference, a
typeface and a palette. AI builds arrive with defaults — Inter, a purple gradient,
a three-column feature grid — and no amount of animation rescues that.

So: direction first, always. Ten minutes here is worth an hour of iteration later.

## Mining references

Never ask a model to invent an aesthetic. Find a real one and adapt it.

**Galleries — craft-level motion and interaction**
- [Godly](https://godly.website) — the best single source for motion-heavy sites
- [Awwwards](https://awwwards.com/websites) — free to browse, filter by category
- [The FWA](https://thefwa.com) — experimental, heavy on WebGL
- [Land-book](https://land-book.com) — real production landing pages
- [Httpster](https://httpster.net), [One Page Love](https://onepagelove.com), [Lapa Ninja](https://lapa.ninja)

**Component registries — free code you can lift**
- [21st.dev](https://21st.dev) — shadcn/Tailwind components; search is free, ~2 installs/day free. A `magic` MCP exists for it but is **not connected here**
- [Aceternity UI](https://ui.aceternity.com) — 200+ free React/Tailwind/Motion components, the maximalist end
- [Magic UI](https://magicui.design) — 150+ free animated components, more restrained
- [React Bits](https://reactbits.dev) — 110+ components, many CSS-only (no RSC constraints)
- [Motion Primitives](https://motion-primitives.com), [Cult UI](https://cult-ui.com), [Origin UI](https://originui.com)
- [Codrops](https://tympanus.net/codrops) — the deepest free archive of motion techniques with working demos

**Boards** — Dribbble and Pinterest for texture, type pairings and layout ideas.
Filter hard: on Pinterest, ignore anything under ~300 likes, and on Dribbble set
the filter to the last month. Popularity is a weak signal but it is better than
none, and low-engagement pins are usually reposts with no source.

**motionsites.ai** is a *paid* library of hero prompts and animated templates
(subscription or lifetime). The video that popularised it implies free access;
the actual product is premium with a limited free tier. Say so plainly if the
user expects otherwise — the free alternatives above cover most of the same
ground.

## Extracting direction from a reference

Do not screenshot a reference and ask for "something like this". Name the parts,
because those are what transfer:

1. **Structure** — how many sections, what does the hero do, where does motion sit
2. **Type** — the actual families, weights, and the size ratio between headline
   and body. Get the real names; "a modern sans" is not a decision
3. **Palette** — background, foreground, one accent. Count them: if you need more
   than three plus neutrals, the direction is not settled yet
4. **Surface** — flat, glass, gradient mesh, film grain, noise
5. **Motion vocabulary** — what moves, how fast, triggered by what
6. **Density** — generous or tight. This is the one people skip and it does more
   work than any other variable

Then rebuild those decisions in the user's own content. The output should be
recognisably *related* to the reference without being a copy — and the way you
get there is by transferring the system, not the pixels.

## Lock the typography

Left alone, models reach for Inter and system defaults, and the result reads as
templated no matter what else you do. Decide the pairing explicitly and put it in
the code before the first section is built.

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@300;400;500&display=swap" rel="stylesheet">
```

Pairings that consistently avoid the generic read:

| Direction | Display | Body |
|---|---|---|
| Editorial, expensive | Instrument Serif *(italic for accents)* | Geist / Inter |
| Technical, precise | Geist | Geist Mono |
| Bold, kinetic | Bricolage Grotesque | Inter Tight |
| Warm, human | Fraunces | Work Sans |
| Swiss, restrained | Neue Haas Grotesk / Inter Tight | same, lighter weight |

Two families is a system; four is a mess. What separates a designed page from a
default one is usually not the font choice but the **tracking on large text** —
headlines above ~40px need negative letter-spacing (`-0.03em` to `-0.05em`) or
they look loose and amateur. Set it once in the token layer and it applies
everywhere.

## Tokens before sections

Write these before building anything. They are what makes a page cohere and what
makes a later change a one-line edit rather than a hunt.

```css
:root {
  --bg: #060607;  --surface: #0e0f12;  --fg: #f4f4f5;
  --muted: #8b8b94;  --accent: #c8ff3d;
  --step--1: clamp(.86rem, .82rem + .2vw, .95rem);
  --step-0:  clamp(1rem, .95rem + .25vw, 1.12rem);
  --step-3:  clamp(2.4rem, 1.8rem + 3vw, 4.2rem);
  --step-5:  clamp(3.6rem, 2.2rem + 7vw, 8.5rem);
  --space-s: .75rem; --space-m: 1.5rem; --space-l: 4rem; --space-xl: 9rem;
  --ease: cubic-bezier(.22, 1, .36, 1);
  --radius: 18px;
}
```

Fluid `clamp()` type removes an entire class of breakpoint bugs, and one shared
`--ease` is why a page's motion feels like it came from one hand.

## What actually makes a page read as AI-built

Run this before showing anything. Each item is a specific, fixable tell:

- **Inter + purple/blue gradient + glassmorphic cards.** The house style of every
  AI builder. Changing only the palette does not help; change the type too.
- **Everything centred.** Real layouts have asymmetry, an off-centre focal point,
  something breaking the grid.
- **Uniform section rhythm.** Six sections of identical height and padding reads
  as generated. Vary the density deliberately.
- **Emoji as icons.** Instant tell. Use a real icon set or none.
- **Three-column feature grid with icon-title-paragraph.** The single most
  over-generated layout on the web.
- **Stock-photo people.** Generated abstract texture beats a fake team photo.
- **No texture at all.** Flat fills read as unfinished. Grain, noise, a gradient
  mesh, or imagery — pick one.
- **Motion on everything.** One signature effect per viewport; the rest supports.
- **Copy that says "Elevate your workflow with cutting-edge solutions".** Nothing
  visual survives filler copy. Push for real sentences about the real thing.

## Working the direction out with the user

This part is genuinely collaborative — you cannot infer taste, and guessing wastes
a whole build.

Use AskUserQuestion with **concrete named options**, not open questions. "What
mood — cinematic dark, bright kinetic, or restrained editorial?" gets an answer;
"what look do you want?" gets a shrug. Offer specific typefaces and specific
reference sites, because people recognise what they like far more reliably than
they can describe it.

Then show, don't tell. Build the hero first at full fidelity — hero plus locked
type and palette is about 90% of the design decision — serve it, screenshot it,
and get a reaction before building seven more sections on top of an unvalidated
direction.

If they can only say something feels "off", walk the checklist above out loud.
It is nearly always type, density, or the centred-everything problem, and naming
it turns a vague dissatisfaction into a change you can make.

Related: `web-motion-primitives` for the effects, `motion-asset-studio` for
generating imagery that matches the direction you just set, and
`motion-web-pipeline` for the end-to-end sequence.
