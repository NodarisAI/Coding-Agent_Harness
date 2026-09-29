---
name: creative-studio
description: Use first for any request about animation, motion, transitions, micro-interactions, scroll effects, 3D or WebGL, video, reels, shorts, launch films, demo or explainer videos, editing, trimming or captioning a video, GIFs, terminal splash screens, ASCII banners, spinners or CLI animation. Routes the request to the one skill and tool that fits (product-film, the web motion skills, Remotion, Hyperframes, ffmpeg, VHS or terminal-motion) and sets the rules every creative job follows, so the work starts from a proven method instead of an improvised one.
---

# Creative studio: pick the right skill and tool before making anything

This is the entry point for motion and video work. Read the request, find its row below, load that skill, and use
the named tool. If two rows match, the more specific one wins. If none matches, ask one question with concrete
options instead of guessing.

## Routing table
| The request sounds like | Load | Tool |
|---|---|---|
| "Launch video", "demo clip", "brag about this", "turn the product into a video" | `product-film` | HTML frames, headless browser, ffmpeg |
| "Explainer video", "reel from our templates", parameterised or data-driven videos in a React codebase | `video-toolkit` (engine choice) | Remotion, pinned |
| "Compose a video in HTML", Hyperframes blocks or catalogue, an agent-built motion graphic | `video-toolkit` (engine choice) | Hyperframes, pinned |
| "Trim", "cut", "join", "crossfade", "add music", "duck the music", "captions", "make it vertical", "square version", "GIF of this" | `video-toolkit` (recipes) | ffmpeg |
| "Record the terminal", "demo GIF of the CLI", "tape" | `terminal-motion` (VHS reference) | charmbracelet VHS, then ffmpeg |
| "Splash screen", "ASCII banner", "rainbow or gradient text", "spinner", "progress bar", "make the CLI feel alive" | `terminal-motion` | Standard library first; rich, ink, ora or Lip Gloss when already a dependency |
| "Make the site feel premium", "not like AI", "what should it look like", "timing and easing" | `motion-design-direction` | References, tokens, motion principles |
| A whole animated website or landing page | `motion-web-pipeline` | Orchestrates the web skills in order |
| One named effect: spotlight mask, reveal on scroll, liquid glass, magnetic cursor, looping video background | `web-motion-primitives` | The tested component in `assets/` |
| Scroll choreography, pinning, text reveals, GSAP, ScrollTrigger, Lenis | `gsap-scroll-motion` | GSAP, pinned |
| 3D, WebGL, Three.js, React Three Fiber, shaders, particles, GLTF | `threejs-webgl-scenes` | Three.js or R3F, pinned |
| UI micro-interactions: hover, press, open and close, page transitions | `motion-design-direction` (motion principles) | CSS transitions or the project's existing motion library |

## Non-negotiables
- **One-sentence intent first.** Before building, write what the motion or video is for in one sentence ("A 20 s
  landscape film showing a claim moving from upload to approval, polished tone") and get a yes. No sentence, no build.
- **Pin every package version.** Exact versions in `package.json`, `requirements.txt` or `go.mod` and the lock
  file. Never run `npx`, `uvx`, `pipx run` or `go run pkg@latest` at an unpinned version, and install nothing new
  without asking.
- **Look at stills before a full render.** Pull frames or a contact sheet (`video-toolkit`, recipe 12) and look at
  them: text fits, nothing collides, contrast holds, no private data is on screen. Screenshot web motion at rest and
  mid-transition.
- **Licensed audio only.** Audio the person supplied or audio whose licence allows the use; record the source.
- **True claims only.** Numbers and customer names appear only when the repository or the person supplied them.
- **Reduced motion is a designed state.** `prefers-reduced-motion` on the web; `NO_COLOR`, `TERM=dumb`, non-TTY, `CI`
  and a flag in a terminal.
- **No emoji in professional output**, and product text follows the project's copy standard: sentence case, full
  sentences, plain English.
- **Everything stays local.** Renders are not uploaded, posted or published (for example `hyperframes publish`)
  unless the person asks for that specific upload.

## When to stop and ask
- The tone, audience or length is not stated and changes the result (a customer-facing film against an internal
  demo).
- The work needs a new dependency, a paid service, a generated-media model with credit cost, or an upload.
- The source footage, screens or site belong to a client or partner and the person has not said they may be used.
- Screens would show real personal, patient, customer or financial data. Use synthetic data or stop.
- No licensed audio is available and the piece needs sound.
- Two routes are equally plausible (for example Remotion against Hyperframes in a project that has neither). Offer
  both with one line on the trade-off and let the person choose.
