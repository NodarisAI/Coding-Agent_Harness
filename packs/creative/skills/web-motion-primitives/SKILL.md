---
name: web-motion-primitives
description: >
  Production-tested motion effect components for immersive web UI — spotlight
  hover video masks, scroll reveal masks, seamlessly looping video backgrounds,
  pinned scroll text carousels, liquid glass surfaces, parallax depth stacks,
  magnetic cursors and WebGL displaced-texture heroes. Use this skill whenever
  the user asks for an animated, interactive, "alive", cinematic, immersive, 3D
  or premium-feeling website or landing page; whenever they name any of these
  effects; whenever they say a site "looks static", "looks like AI slop" or
  "needs motion"; and whenever they want the kind of hero section seen on
  Awwwards, Dribbble, motionsites.ai or 21st.dev. Also use it before writing any
  scroll-linked, hover-reveal, cursor-driven or video-background effect from
  scratch — these are already built, debugged and accessible, so reach for them
  first rather than reinventing the wheel.
---

# Web Motion Primitives

Eight effects that carry most of what makes a site feel expensive. Each lives in
`assets/` as a standalone HTML file you can open, watch, and lift from directly.
They are not sketches — every one was run in a browser and three had real bugs
that only surfaced there (documented below, because those failure modes recur).

## Working with the user, not at them

These effects are *aesthetic* decisions. You cannot evaluate them from source,
and neither can the user from a description. The loop that actually works:

1. Ask what feeling they're after before picking effects — use AskUserQuestion
   with concrete options ("cinematic and dark", "bright and kinetic", "restrained
   and editorial"), not open-ended questions they have to design an answer to.
2. Build it, then **serve it and screenshot it**. `python3 -m http.server` in the
   directory, `preview_start` the URL, screenshot. Show them, don't describe.
3. Change one variable at a time and re-screenshot. Motion work converges through
   iteration; a single big reveal almost always misses.

Never hand over motion you haven't looked at. The whole category fails silently —
a mistuned easing curve throws no error, it just feels cheap.

## The eight primitives

| File | Effect | Reach for it when |
|---|---|---|
| `spotlight-video-mask.html` | Cursor reveals video through a circular mask over a still | Portfolio/case-study tiles, "hover to see it move" |
| `reveal-mask-scroll.html` | Two renders of one frame, wiped between on scroll | Before/after, transformation narratives, product states |
| `seamless-loop-video.html` | Dual-buffer cross-faded video background | Any full-bleed video hero (see the trap below) |
| `scroll-text-carousel.html` | Pinned split section, one item per scroll beat | Process/feature lists you need actually read |
| `liquid-glass.html` | Blurred surface, specular edge, moving sheen | Cards, nav, CTAs floating over imagery or video |
| `parallax-depth-stack.html` | Flat layers in real CSS 3D perspective | Depth without the cost of a WebGL scene |
| `magnetic-cursor.html` | Custom cursor + buttons that reach for it | Interactive polish on CTAs |
| `webgl-hero-plane.html` | Three.js shader: noise displacement + chromatic aberration | The hero, when a plain image isn't enough |

Read the file before adapting it — the comments explain *why* each decision was
made, which is what you need in order to change it safely.

## Three bugs these files already fixed for you

These recur in every hand-rolled version of these effects. Preserve the fixes.

**Gating a render loop on `document.hidden` ships a black hero.** A page loading
in a background tab, or restoring from bfcache, has `document.hidden === true`.
Skip the render and the canvas never paints a first frame. Gate *animation* on
visibility; always paint at least one frame on asset load, resize, and on
becoming visible. This applies to any canvas, WebGL or 2D.

**Two rAF loops racing.** When more than one code path can start the loop (a
swap handler *and* a visibilitychange handler, typically), you get two loops
each tripping the same threshold. Always `cancelAnimationFrame` before starting.

**Magnetic elements measuring their own displaced box.** `getBoundingClientRect`
returns the *moved* position, so the element computes distance from where it
already went and chases its own tail. Track the applied translation and subtract
it to recover rest geometry. Same class of bug afflicts anything that both reads
and writes layout position.

## Non-negotiables

**Reduced motion.** Every file honours `prefers-reduced-motion`, and the fallback
is a *designed* state, not an absence. A spotlight mask with no pointer reveal is
a dead still frame — so the reduced-motion path shows the video outright instead.
Ask "what does this look like with the motion removed?" and make that good.

**Video is the expensive thing.** Autoplaying video is the largest cost on these
pages by an order of magnitude. Play only what's visible, pause on
`visibilitychange`, and never stack six autoplaying backgrounds. Serve H.264 MP4
for compatibility plus WebM where you can, and keep hero loops under ~3 MB.

**WebGL costs are dominated by pixel count.** `setPixelRatio(Math.min(devicePixelRatio, 2))`
is the single highest-leverage line in a WebGL hero — uncapped 3× retina renders
~9× the pixels for a difference nobody sees through a blur.

**Keyboard access survives.** Custom cursors are decoration layered on top; the
real element stays focusable and hit-testable, with visible `:focus-visible`.

**CDN scripts need integrity hashes.** The demo files load GSAP and Three from a
CDN for portability. In anything you ship, either `npm install` them or add
`integrity="sha384-..." crossorigin="anonymous"` — an unpinned CDN script is a
supply-chain hole in a page you're handing to a client.

## Porting to React

The demos are vanilla so they run anywhere and are directly viewable. In React:

- Wrap setup in `useLayoutEffect`, and for anything GSAP use `gsap.context(() => {...}, ref)`
  then `return () => ctx.revert()`. Without revert, Strict Mode's double-invoke
  leaves duplicate ScrollTriggers and the pinning goes wrong in ways that are
  miserable to debug.
- Keep pointer/scroll values in refs, never state — a `setState` per pointermove
  re-renders the tree 120 times a second.
- Write to `element.style.setProperty('--x', ...)` directly. CSS custom properties
  are the clean seam between React's render model and per-frame animation.

## Composing a page

Effects compound badly. A hero with a WebGL plane, a magnetic cursor, a spotlight
tile *and* a pinned carousel reads as a demo reel, not a product. The pattern that
holds up: **one signature effect per viewport**, with quieter motion between them.
Pick the loudest one for the hero and let the rest support it.

Related skills: `motion-design-direction` for choosing the aesthetic and locking
type before you build, `gsap-scroll-motion` for choreography across sections,
`threejs-webgl-scenes` for going beyond a single shader plane, and
`motion-asset-studio` for generating the imagery and video these effects display.
