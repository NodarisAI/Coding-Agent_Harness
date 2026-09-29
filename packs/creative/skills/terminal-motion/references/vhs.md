# Recording a terminal demo with VHS

VHS (https://github.com/charmbracelet/vhs, release v0.12.1 on 2026-09-29) runs a scripted terminal session from a
`.tape` file and writes GIF, MP4, WebM or a PNG frame folder. Install with `brew install vhs` (it depends on `ttyd`
and `ffmpeg`); ask before installing.

## A starting tape

```
Output demo.gif
Output demo.mp4

Require python3

Set Shell "bash"
Set FontSize 28
Set Width 1200
Set Height 600
Set Padding 24
Set Framerate 30
Set TypingSpeed 40ms
Set Theme "Catppuccin Mocha"

Hide
Type "cd examples && clear"
Enter
Show

Type "COLORTERM=truecolor python3 splash.py"
Sleep 300ms
Enter
Wait+Screen /Loading configuration/
Sleep 2s
Screenshot demo-final.png
```

Run it with `vhs demo.tape`. Use `Hide` and `Show` around setup commands, `Wait` with a pattern instead of long
fixed `Sleep`s, and set `COLORTERM=truecolor` explicitly so the recording shows the colours your users will see.

## Checks before sharing
- Look at `demo-final.png` and at stills pulled from the MP4 (`video-toolkit`, contact sheet recipe) before
  sharing: no private paths, host names, tokens or real customer data on screen.
- Keep the GIF under about 5 MB for a README; if it is larger, lower the framerate or width, or share the MP4.
- Probe the MP4 with `ffprobe` to confirm duration and size.

Status in this harness: VHS was not installed on the build machine on 2026-09-29, so the tape above was not run
here. The syntax was checked against the VHS command reference.
