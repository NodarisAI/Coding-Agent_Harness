---
name: product-film
description: Use when someone wants a short video of what they built — "make a launch video", "turn this into a video", "brag about this", "a demo clip for the pilot", "show customers what it does" — from the project's own code or a website URL, for any kind of product. Builds a 15–25 second film in code from the product's real screens, with synthetic data only, true claims only, and a look at every scene before anything is rendered or shared (plus an automated patient-data check when the screens show health data).
---

# Product film: a short video of the real product, safe to share

A person asks for a video of what they built. You make it yourself in code: story, visuals, sound and render. The
film shows the working product doing its job, in its own colours and fonts, for 15 to 25 seconds.

Adapted from `/brag-slim` in latent-spaces/brag (MIT), rebuilt for our harness: no third-party render package, no
real personal data on any frame, and no claim the product has not earned.

## Rules that come before creativity
- **Synthetic data only.** Every screen is filled from the repository's synthetic seed or a fixture made for the film.
  Never capture a screen that has shown real customer, user, financial or personal data, even if you plan to blur it.
- **Check every frame.** Before rendering, look at a still of every scene yourself and read its visible text: no real
  names, email addresses, account numbers, keys, internal host names or private paths.
- **When the screens show patient data** (any health, member or claim screen), also pipe the visible text of each
  scene (the DOM text you capture) into `nodaris-harness redact --check`. Exit code 1 means it found a name, date of
  birth, member or claim id, or address: the render stops until that value is replaced with a synthetic one.
- **True claims only.** Numbers, customers, results and testimonials appear only if they are in the repository or
  the person gave them to you in this session. Say "faster claim review", never "cuts denials 40%" without a source.
  If a number is unsourced, leave it out.
- **Product text is professional English.** Follow the project's copy standard if it has one: sentence case, full
  sentences, no internal jargon.
- **Everything stays local.** Downloads, frames and renders stay in the output folder. Nothing is uploaded or
  posted; the person decides where the film goes.

## 1. Inspect
Decide the input:
- **Project** (no input, and the current folder is a project): read the main page, styles (exact colours and fonts),
  README, routes and key components. Render the project's real components with synthetic data rather than
  redrawing them.
- **Website** (a URL): load it in a headless browser, dismiss overlays, scroll section by section, and take the
  copy, colours, fonts, logo and product images. A client's or partner's site needs the person's say-so first.

Then answer before planning: what it is in one sentence; who it is for and what it does for them; what sets it
apart; the strongest true claim; the visual hook; the flow to show (entry, key action, result); the tone; the
one-line caption.

## 2. Plan
Write `film-plan.md` in the output folder: angle, hook, two or three highlights, closing line, tone, colours and
fonts, and a scene list with durations that add up to the target. Default shape: hook (2–3 s), reveal (2–4 s), two
or three highlights, close (2–4 s). If the person points at one feature or release, the film is about that.

Tones: `polished` (the default for business buyers: restrained, long holds, soft fades), `app-store` (clean
feature cards), `cinematic` (large type, slow moves), `default` (playful, clean). Parody and chaotic tones only when
the person asks, and never for a customer-facing film.

## 3. Build, check, render
- Draw the film as an HTML page where every frame is a pure function of time. Wait for fonts and images before
  capturing a frame. Capture frames with a headless browser and encode with ffmpeg (both already on the machine;
  install nothing without asking). The encode, audio, reframing and caption recipes are in `video-toolkit`.
- Readable text stays fully visible for about 0.3 s per word once the whole line is on screen.
- Before the full render, look at stills from every scene and from the middle of every transition. Fix overflow,
  collisions and low contrast. A crossfade between two busy layouts looks muddy: move old content out, then new
  content in.
- Sound: music and effects mixed as one piece, effects soft under the music. No voiceover unless asked. Use only
  audio the person supplied or audio whose licence allows it, and record the source in `film-plan.md`.
- Output sizes: landscape 1920×1080, vertical 1080×1920, square 1080×1080, 30 fps.

## 4. Deliver
- `film.mp4`, and `film.jpg`: the strongest settled frame, also baked in as frame 0 (replace it, do not add one,
  so the duration and audio stay in sync).
- `share-copy.txt`: one to three specific sentences in the film's tone, with no claim the film does not support.
- Tell the person where the files are, the angle in one sentence, the result of the frame check (and of the
  patient-data check when it applied), and offer
  to redo a scene or try another tone.
