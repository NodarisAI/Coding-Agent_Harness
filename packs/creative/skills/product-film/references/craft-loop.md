# The craft loop: how a film stops looking generated

A first render from a model looks like a slideshow: stills that fade, text that appears, music laid under it.
What separates a film people watch twice is not a better engine. It is a way of working: decide the feeling
first, borrow structure from a film that already works, time every beat to the music, then look at the result
frame by frame and fix it, several times, before anyone else sees it.

This method comes from Nate Herk's walkthrough "Opus 5.5 Just Changed Video Editing Forever" (2026), rebuilt
for our tools. It was tested on 29 September 2026 by remaking a 60-second film of a 1936–1939 Rolls-Royce
Phantom III from three daylight photographs. The first version, made without this loop, was judged "pretty
garbage". The worked example at the end records what the loop changed.

## 1. Write the emotional brief before anything else
One paragraph, in `BRIEF.md`, that says what the viewer should feel, not what they should see.
"Dread that turns into reverence: the city is dangerous and the car is the calmest thing in it" produces
different choices from "show the car at night". Then list what the material can honestly carry. Three daylight
photographs cannot be shown driving through a night street as if filmed; they can emerge from darkness under
moving light. Write that constraint down so nobody fakes around it later.

## 2. Borrow structure from a reference film
Pick one film the person likes, or one they point you to. Watch it (the `/watch` skill gives frames and a
transcript) and write down, in plain words, why it works:
- the pace: how long a shot holds and where the cuts land against the beat;
- the chrome: title-safe frame, corner brackets, brand and title top-left, format and tempo top-right,
  timecode bottom-left, chapter marker bottom-right, a segmented progress bar;
- the type: one display face used large, one monospaced face used small for telemetry and callouts;
- the moves: light sweeps, kinetic type walls, lookbook callouts with leader lines, a typed end line.
Keep this analysis as a short note beside the film. When the person likes a result, turn the note into a
skill or add it to this file, so the next film starts from it.

## 3. Plan every beat against the music
Choose the tempo first. At 72 BPM a beat is 0.833 s and a bar 3.333 s; 18 bars make 60 s. Write
`STORYBOARD.md` as a table: chapter, start and end time on a bar line, picture, sound, and the line of script.
Cuts land on bars, hits land on cuts, and every on-screen line has a start time. Sound design is planned in the
same table, not added at the end.

## 4. Build with layers, not slides
In Hyperframes (see `video-toolkit`), a cinematic composition is a stack:
1. a real-time layer (Three.js) that carries motion the stills cannot: a street, weather, a camera move;
2. the hero images, each as a dark base copy, a lit copy revealed by a moving mask (a light sweep), a flipped
   and blurred reflection, and screen-blended glows where light sources are;
3. foreground weather drawn from time, so it can slow down or freeze on a story beat;
4. type, one line at a time, character by character, with blur resolving to sharp;
5. finish: grain, vignette, letterbox, and the HUD chrome.
Every value is a function of time. No `Date.now()`, no unseeded randomness, no free-running loops.

## 5. Make the sound yourself when you have to
Music the person supplied comes first. When there is none, a local model can make a bed: MusicGen small ran on
an Apple-silicon GPU at about 250 s per 22 s chunk; the medium model hung and had to be stopped. Generate in
chunks, conditioning each on the last 6 s of the previous one, and prompt each chunk for its part of the arc
(brooding, building, climax). Synthesize the ambience (rain from shaped noise, thunder from low-passed noise
with a slow envelope) and place effects on the cuts in the same script, then normalise the peak to −1 dB.
Record every source and licence next to the render.

## 6. Verify frame by frame, then iterate
This is the step that matters most, and the one a first attempt skips.
1. Render still frames at the middle of every chapter and look at them as a contact sheet.
2. Render the film, then pull a strip of frames just before and after every cut (`video-toolkit`, recipe 13).
3. Write down what is wrong in one line each: unreadable, too dark, reads as a slideshow, a hard crop edge, a
   line colliding with an image, a cut off the beat.
4. Fix and repeat. Plan for at least three passes before the person sees it, and stop only when a pass finds
   nothing worth fixing.

## 7. Learn from the person's reaction
After the person watches it, record what they liked and disliked in this file or the reference note, with the
date. The next film reads it first.

## Worked example: Phantom III, "Nocturne" (29 September 2026)
- **Brief:** dark, broody, luxury and elegance, in the spirit of a caped-crusader film without borrowing any of
  its names or lines. Eight original lines, from "The city belongs to the night." to "Some legends don't need
  a mask."
- **Structure:** eight chapters on bar lines at 72 BPM; a HUD with chapter markers, timecode, telemetry and a
  segmented progress bar; 2.39:1 letterbox.
- **Pass 1 findings (stills):** the 3D city read as floating squares because the sky was hidden and buildings
  were no darker than the fog; the detail crops showed hard rectangular edges; the side view of the car was
  almost invisible; window grids behind the car distracted.
- **Pass 1 fixes:** a higher opening camera so the skyline sits against the sky; fog darker than the horizon so
  buildings read as silhouettes; smaller, dimmer windows; depth-of-field blur on the street behind each car;
  soft radial masks on the crops; a brighter side view.
- **Pass 2 findings (frame strip around every cut):** the detail shots stayed nearly black because their fade
  was tied to the full shot length. Fix: a 0.45 s fade in, with the slow push kept separate.
- **What worked:** the car emerging from black under a light sweep, headlamps igniting with anamorphic
  streaks, a lightning strike that freezes the rain mid-air while the sound drops out, and the wordmark
  assembling on the downbeat.
