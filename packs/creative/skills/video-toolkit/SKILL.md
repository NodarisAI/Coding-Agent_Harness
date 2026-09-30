---
name: video-toolkit
description: Use when a video has to be generated or edited in code - trimming, cutting, concatenating, crossfading, a Ken Burns pan and zoom on a still, burned-in or soft captions, adding or ducking music, reframing to vertical 9:16 or square 1:1 for reels and shorts, turning frames into an MP4, making a GIF, or checking a render with ffprobe. Also use to choose between Remotion, Hyperframes, HTML frames captured by a headless browser, and VHS before starting a video build. Every ffmpeg recipe here was run and probed on ffmpeg 8.1.
---

# Video toolkit: generate and edit video in code

Pick the engine first, then use the tested ffmpeg recipes for everything around it. Recipes were run on
2026-09-29 with ffmpeg 8.1 (Homebrew, `/opt/homebrew/bin/ffmpeg`) against generated inputs
(`-f lavfi -i testsrc` and `sine`), and every output was checked with `ffprobe`.

## Choose the engine
| The job | Use | Why |
|---|---|---|
| A short film of a product's real screens, 15-25 s | `product-film` skill (HTML page as a function of time, headless browser frames, ffmpeg) | No new dependency; the real components render with synthetic data. |
| A React team wants reusable, parameterised video templates | Remotion (`remotion@4.0.529`, `@remotion/cli@4.0.529`) | Compositions are React components; props drive variants. Read its licence first: larger companies need a paid company licence. |
| An agent writes HTML compositions with media timing and catalogue blocks, or a cinematic film with a Three.js layer | Hyperframes (`hyperframes@0.8.91`, Apache-2.0, HeyGen) | HTML in, MP4 out, built for agents. Never run `hyperframes publish`; it uploads. See "Hyperframes, verified" below. |
| A recording of a terminal session | VHS (`terminal-motion` skill) | The tape is code, so the demo is reproducible. |
| Editing footage that already exists | ffmpeg recipes below | Deterministic, scriptable, already installed. |

Add a package only with the person's agreement, pinned exactly in `package.json` and the lock file. Run it through
the project's own scripts or `npx <pkg>@<exact version>`; never an unpinned `npx`.

## Rules
- **Stills before a full render.** Pull a contact sheet (recipe 12) or single frames and look at them. A full render
  is the last step, not the way to find a mistake.
- **Licensed audio only.** Music and effects the person supplied, or audio whose licence permits this use; record
  the source and licence next to the render.
- **H.264, yuv420p, even dimensions, `+faststart`** for anything that will be shared. Other pixel formats play in
  ffmpeg and fail in browsers and phones.
- **Always pass `-nostdin`** when ffmpeg runs from a script or heredoc; otherwise it reads the script as keyboard
  commands.
- **No emoji** in captions or titles for professional output.

## Recipes (all verified)
Set `F="/opt/homebrew/bin/ffmpeg -nostdin -hide_banner -loglevel error -y"` in bash, or write the full command.

1. **Image sequence to MP4** (odd sizes rounded down to even; 1279×719 became 1278×718):
   `$F -framerate 30 -i frames/frame_%04d.png -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -c:v libx264 -pix_fmt yuv420p -crf 18 -movflags +faststart out.mp4`
2. **Ken Burns on a still** (5 s slow push-in; upscale first so the zoom does not jitter):
   `$F -loop 1 -framerate 30 -i still.png -vf "scale=3840:-2,zoompan=z='min(zoom+0.0008,1.2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=150:s=1920x1080:fps=30" -frames:v 150 -c:v libx264 -pix_fmt yuv420p kenburns.mp4`
3. **Crossfade two clips** (offset = first clip length minus fade; two 4 s clips gave 7 s):
   `$F -i a.mp4 -i b.mp4 -filter_complex "[0:v][1:v]xfade=transition=fade:duration=1:offset=3,format=yuv420p[v];[0:a][1:a]acrossfade=d=1[a]" -map "[v]" -map "[a]" -c:v libx264 -c:a aac xfade.mp4`
   Both clips need the same size, frame rate and timebase; scale and `fps=30` them first if not.
4. **Trim.** Frame-accurate: `$F -ss 1 -i a.mp4 -t 2 -c:v libx264 -pix_fmt yuv420p -c:a aac trim.mp4` (2.000 s).
   Fast, no re-encode: `$F -ss 1 -i a.mp4 -t 2 -c copy trim.mp4` (2.100 s; cuts snap to keyframes).
5. **Concatenate.** Same codec and size: write `file '/abs/a.mp4'` lines to `list.txt`, then
   `$F -f concat -safe 0 -i list.txt -c copy joined.mp4`. Different sizes:
   `$F -i a.mp4 -i b.mp4 -filter_complex "[0:v]scale=1920:1080,setsar=1[v0];[1:v]scale=1920:1080,setsar=1[v1];[v0][v1]concat=n=2:v=1:a=0[v]" -map "[v]" -c:v libx264 -pix_fmt yuv420p joined.mp4`
6. **Add music under existing audio** (music at 30%, cut to the video's length):
   `$F -i a.mp4 -i music.m4a -filter_complex "[1:a]volume=0.3[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]" -map 0:v -map "[a]" -c:v copy -c:a aac out.mp4`
7. **Duck music under a voice** (measured: music 6.4 dB lower while the voice plays, full level after; `apad`
   keeps the music running past the end of the voice):
   `$F -i voice.m4a -i music.m4a -filter_complex "[0:a]asplit=2[vo][sc0];[sc0]apad[sc];[1:a][sc]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=300[duck];[duck][vo]amix=inputs=2:duration=longest:normalize=0[a]" -map "[a]" -c:a aac mix.m4a`
   Lower `threshold` or raise `ratio` for deeper ducking.
8. **Reframe.** Vertical 9:16 centre crop: `$F -i a.mp4 -vf "crop=ih*9/16:ih,scale=1080:1920,setsar=1" -c:v libx264 -pix_fmt yuv420p -c:a copy vertical.mp4`.
   Vertical with the full frame over a blurred fill:
   `$F -i a.mp4 -filter_complex "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:1[bg];[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]" -map "[v]" -map 0:a -c:v libx264 -pix_fmt yuv420p -c:a copy vertical.mp4`.
   Square 1:1: `$F -i a.mp4 -vf "crop=ih:ih,scale=1080:1080,setsar=1" -c:v libx264 -pix_fmt yuv420p -c:a copy square.mp4`.
9. **GIF with a palette** (640 px, 15 fps, loops):
   `$F -i a.mp4 -vf "fps=15,scale=640:-1:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5" -loop 0 out.gif`
10. **Soft captions** (a selectable track, no re-encode):
    `$F -i a.mp4 -i captions.srt -map 0 -map 1 -c:v copy -c:a copy -c:s mov_text -metadata:s:s:0 language=eng out.mp4`
11. **Burned-in captions.** The Homebrew ffmpeg 8.1 bottle has no `drawtext` or `subtitles` filter (no freetype or
    libass), so render each caption to a transparent PNG (Pillow, or a headless-browser screenshot of the product's
    own type) and overlay it for its time range:
    `$F -i a.mp4 -i cap1.png -i cap2.png -filter_complex "[0:v][1:v]overlay=enable='between(t,0.5,2.0)'[v1];[v1][2:v]overlay=enable='between(t,2.2,3.8)',format=yuv420p[v]" -map "[v]" -map 0:a -c:v libx264 -c:a copy burned.mp4`
    See `references/caption_png.py` for the Pillow renderer used in the test. Check first with
    `ffmpeg -hide_banner -filters | grep -E " (drawtext|subtitles) "`; if a build has them, `subtitles=captions.srt`
    is simpler.
12. **Contact sheet of stills** (one frame per second, 4×2 grid):
    `$F -i film.mp4 -vf "fps=1,scale=480:-2,tile=4x2" -frames:v 1 contact.png`. Single frame: `$F -ss 1 -i film.mp4 -frames:v 1 still.png`.

13. **Frame strip around every cut** (the review step after a render; times are the cuts plus or minus 0.2 s):
    `for t in 6.5 6.8 13.2 13.5; do $F -ss $t -i film.mp4 -frames:v 1 -vf scale=480:-2 strip/$t.png; done`, then
    `$F -pattern_type glob -i 'strip/*.png' -vf tile=4x2 -frames:v 1 strip.jpg`. Look at it and fix what it shows.
14. **Loudness per 5 s window** (find silent or clipped stretches without listening; `n` is 5 s at 48 kHz):
    `$F -i mix.wav -af "asetnsamples=n=240000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-" -f null - | grep RMS`
    On the Phantom film this printed −29 … −11 dB per window, with the peaks on the thunder strike and the wordmark hit.

## Hyperframes, verified (29 September 2026, `hyperframes@0.8.91`)
- Commands, from the project folder: `npx hyperframes@0.8.91 lint`, then
  `npx hyperframes@0.8.91 snapshot --at 2.3,9,15.4 --no-end --describe false -o snaps` for stills, then
  `npx hyperframes@0.8.91 render -f 30 -q standard -w 4 -o renders/film.mp4`. A 60 s 1080p film with a
  Three.js layer rendered in 4 min 14 s on an M-series Mac.
- Set `HYPERFRAMES_NO_TELEMETRY=1` (or `DO_NOT_TRACK=1`) so renders send no usage data. `--describe` sends frames
  to Gemini when a key is set; pass `--describe false` unless the person agreed.
- The lint errors that matter: a `<audio>` needs an `id` or the render is silent; do not tween `letterSpacing`
  or margins (animate each glyph's `x`); give repeated `fromTo` tweens on one element a `tl.set` baseline.
- A Three.js layer renders from the `hf-seek` event and needs `data-duration` on the root. Register
  `window.__hf.buildReady.<name>` so the first frame waits for the scene.
- If the render fails with "Chrome cannot start ... ETIMEDOUT", the machine is too busy (a model generating on
  the GPU); run it again once the load drops.

## Check the result
`ffprobe -v error -show_entries format=duration:stream=codec_type,codec_name,width,height,pix_fmt -of compact=p=0 out.mp4`
confirms duration, codecs, size and `yuv420p`. For loudness, `ffmpeg -nostdin -i out.mp4 -af volumedetect -f null -`
prints mean and max volume; keep peaks below about -1 dB. Then look at the contact sheet.

## Not verified here
Remotion renders were not run (it installs packages); VHS was not installed. Follow their own
documentation for the exact render command at the pinned version, and run it once before relying on it.
