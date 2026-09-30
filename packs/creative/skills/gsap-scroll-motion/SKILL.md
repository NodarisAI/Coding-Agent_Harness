---
name: gsap-scroll-motion
description: >
  Scroll choreography and interface animation with GSAP, ScrollTrigger, Lenis
  smooth scroll, SplitText and Flip — pinning, scrubbing, timelines, staggers,
  parallax, page transitions and text reveals. Use this skill whenever the user
  wants scroll-triggered or scroll-linked animation, pinned sections, "animate on
  scroll", parallax, sticky storytelling, smooth/inertia scrolling, staggered
  reveals, animated headline entrances, or page transitions; whenever they
  mention GSAP, ScrollTrigger, ScrollSmoother, Lenis, Locomotive, AOS, or ask how
  to make animations feel smooth rather than janky; and whenever animation needs
  to be sequenced across a whole page rather than added per component. Also use
  it when debugging animation that stutters, fires at the wrong scroll position,
  duplicates in React Strict Mode, or breaks on resize.
---

# GSAP Scroll Motion

GSAP is the least interesting part of good scroll work. What separates a site
that feels choreographed from one that feels twitchy is *pacing* — and pacing is
a design decision you make before touching the API.

## Install it, don't CDN it

GSAP is free including all former Club plugins (ScrollTrigger, SplitText, Flip,
MorphSVG) since v3.13.

```bash
npm i gsap
```

```js
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
gsap.registerPlugin(ScrollTrigger);
```

A CDN `<script>` is fine for a scratch demo. In anything you hand to a client it
needs an `integrity` hash or, better, to be a dependency — an unpinned third-party
script executing on their marketing site is a real supply-chain exposure.

## React: revert or suffer

This is the number one source of "the pinning went weird" bug reports. Strict
Mode mounts effects twice; without cleanup you get duplicate ScrollTriggers
fighting over the same element, and the symptoms are baffling.

```jsx
useLayoutEffect(() => {
  const ctx = gsap.context(() => {
    gsap.to('.card', {
      y: 0, opacity: 1, stagger: 0.08,
      scrollTrigger: { trigger: '.cards', start: 'top 80%' }
    });
  }, root);                 // scope selectors to this component's subtree
  return () => ctx.revert(); // undoes every tween, trigger and inline style
}, []);
```

`useLayoutEffect` rather than `useEffect` so measurement happens before paint —
otherwise elements flash at their untweened position. `gsap.context` gives you
both scoped selectors and one-call cleanup.

## Trigger positions

`start: 'top 80%'` reads as *"when the trigger's top reaches 80% down the
viewport"*. Element position first, viewport position second. Add
`markers: true` while building — it removes all guesswork, and remember to strip
it before shipping.

Sensible defaults:

- **Reveal on entry** — `start: 'top 85%'`, no scrub. Fires slightly before the
  element is comfortably in view, which is what makes it feel responsive rather
  than late.
- **Scrubbed to scroll** — `scrub: 1`. The number is a catch-up delay in seconds;
  `scrub: true` is rigidly locked to the scrollbar and feels mechanical. `1` is
  the value that reads as "weighty" to most people.
- **Pinned sequence** — `pin: true, scrub: true`, with
  `end: () => '+=' + window.innerHeight * n`. Use a function so it recalculates
  on resize instead of freezing a stale pixel value.

## Pacing, which is the actual craft

One viewport of scroll distance per beat. Less and the visitor blows past it;
more and the page feels stuck. If a pinned section needs four states, that is
roughly `+=` four viewport heights — and if that sounds long, the honest fix is
fewer states rather than faster ones.

Durations: 0.3–0.6s for entrances, 0.8–1.2s for a hero. Anything over 1.5s that
the user is waiting on reads as broken rather than luxurious.

Easing: `power2.out` for entrances (fast start, soft landing — the shape of
something arriving). `power2.inOut` for state changes. `back.out(1.4)` sparingly,
for things that should feel playful. Linear only for continuous motion like
marquees. The default `power1.out` is fine and shipping it is not a failure.

Stagger: 0.05–0.1s between siblings. Above ~0.15s the group stops reading as one
gesture and starts reading as a queue.

## matchMedia over manual breakpoint checks

```js
const mm = gsap.matchMedia();

mm.add('(min-width: 861px) and (prefers-reduced-motion: no-preference)', () => {
  const st = ScrollTrigger.create({ /* … */ });
  return () => st.kill();
});
```

Two things this buys you. Reduced-motion is handled as a first-class case rather
than an afterthought, and GSAP reverts every style it wrote when the query stops
matching — so the mobile layout is genuinely clean, not a desktop layout with the
animation switched off. It also self-heals: if the query starts matching later
(rotation, resize, a tab that loaded at zero width), the context is created then.

Pinning on touch fights the browser's own scroll physics and feels broken on iOS.
Gate it above your breakpoint and let mobile be a plain stacked list.

## Smooth scroll: Lenis

```js
import Lenis from 'lenis';
const lenis = new Lenis({ lerp: 0.1 });
lenis.on('scroll', ScrollTrigger.update);
gsap.ticker.add((t) => lenis.raf(t * 1000));
gsap.ticker.lagSmoothing(0);
```

Driving Lenis from GSAP's ticker rather than its own rAF keeps both on one loop —
two independent loops produce a subtle beat-frequency stutter that is maddening
to diagnose.

Be deliberate about whether you want this at all. Smooth scroll overrides a
system behaviour people have calibrated to, it interferes with find-in-page and
anchor jumps, and on a content site it is often a downgrade. Worth it for a
showcase piece; questionable for documentation. `lerp: 0.1` is the ceiling —
higher values feel like lag rather than smoothness.

## Text reveals

SplitText is free now and does the tedious part:

```js
const split = new SplitText('.headline', { type: 'lines,words', linesClass: 'line' });
gsap.from(split.words, { yPercent: 110, opacity: 0, duration: 0.9,
                         stagger: 0.03, ease: 'power3.out' });
```

Wrap lines in `overflow: hidden` containers so words rise out of a mask rather
than fading in place — the mask is what makes it read as typographic rather than
generic.

Two things that bite: split before webfonts load and the line breaks are computed
against the fallback font, so re-split on `document.fonts.ready`. And split text
can wreck screen-reader output — keep an `aria-label` with the intact string on
the container.

## Performance

Animate `transform` and `opacity`. Both are composited; everything else triggers
layout or paint per frame. `y: 100` (which GSAP maps to a transform) rather than
`top: 100px` is the difference between 60fps and 15.

`will-change` is a promise you are about to animate something, not a speed
switch. Applied to twenty elements permanently it exhausts GPU memory and makes
things slower. Set it before, remove it after, or leave it out.

Use `ScrollTrigger.batch()` for many similar elements — one observer instead of
fifty. And call `ScrollTrigger.refresh()` after anything that changes document
height (images loading without dimensions, accordions, route changes), or every
trigger below the change fires at the wrong position.

## Debugging the usual suspects

| Symptom | Cause |
|---|---|
| Fires at the wrong scroll point | Height changed after init — `ScrollTrigger.refresh()` |
| Doubles / behaves erratically in React | Missing `ctx.revert()` in cleanup |
| Stutters while scrolling | Animating layout properties, or Lenis on its own rAF |
| Pin jumps at the start | Pinned element has a margin — use padding on a wrapper |
| Breaks on resize | `end` given as a fixed number instead of a function |
| Nothing happens at all | Plugin not registered, or trigger element not in the DOM yet |

## Working with the user

Scroll pacing cannot be judged from code. Build it, serve it, and *scroll it* —
`preview_start`, then drive the page and screenshot at several positions. Ask
whether it feels too fast or too slow before tuning anything; that is a question
with an answer, unlike "does this look right?".

Related: `web-motion-primitives` has the pinned carousel and scroll reveal mask
already built, `threejs-webgl-scenes` for driving a 3D scene from scroll, and
`motion-design-direction` for deciding what deserves motion in the first place.
