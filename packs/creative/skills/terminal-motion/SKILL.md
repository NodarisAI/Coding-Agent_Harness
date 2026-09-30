---
name: terminal-motion
description: Use when a command-line tool needs a splash screen, ASCII or figlet banner, gradient or rainbow text, a spinner, a progress bar, a typing effect or any other terminal animation, or when someone asks to make a CLI "look good", "feel alive" or "like the Charm tools". Also use before recording a terminal demo to GIF or MP4 with VHS. Covers when motion helps and when it hurts, the opt-outs every animated CLI must honour (NO_COLOR, TERM=dumb, non-TTY, CI, a flag), width handling, restoring the terminal on Ctrl-C, and pinned library choices for Python, Node and Go.
---

# Terminal motion: splash screens, banners and spinners that respect the terminal

A command-line tool is used by people in a hurry, by scripts, by CI logs and by screen readers. Motion is welcome
only when it tells the person something (the tool started, work is happening, work finished) and disappears when
nobody is watching. Everything in this skill follows from that.

Ideas adapted from an MCP-market "animation-skill" (rich and pyfiglet splash screens), rebuilt in our own words
against the Command Line Interface Guidelines and the Charm tools.

## Before writing any motion
State the intent in one sentence and get agreement: "A 0.4 s gradient reveal of the product name on first run, so
the tool feels finished; skipped in pipes, CI and with --no-motion." If you cannot write that sentence, the tool does
not need the animation.

## When motion helps and when it hurts
| Helps | Hurts |
|---|---|
| Acknowledging input within 100 ms, before a slow network call | A splash on every invocation of a command run many times a day |
| A spinner or progress bar for work longer than about 1 s | Animation written into a log file, a pipe or CI output |
| A one-time welcome on first run or `init` | A typing effect on output the person needs to read or copy |
| A final result line that replaces the spinner | Colour as the only carrier of meaning (red means failed) |

## Hard rules
- **Splash under 2 s, and skippable.** Any key ends it (read stdin with `select` and a short timeout). Show it on
  first run, `init` or a bare command with no arguments, never before `--help`, `--version` or machine output.
- **Transient by default.** A spinner or progress bar clears its own line (`\r` then `ESC[2K`) and is replaced by
  one permanent result line. Nothing animated is left in scrollback.
- **Turn all motion and colour off when any of these hold:** stdout is not a TTY; `NO_COLOR` is set to anything
  (https://no-color.org); `TERM=dumb` or unset; `CI` is set; the user passes `--no-color` or `--no-motion`; the tool's
  own variable (for example `MYTOOL_NO_MOTION`) is set. Colour off implies motion off. Offer `--plain` or `--json`
  for scripts.
- **Truecolor only when advertised.** Use 24-bit escapes (`ESC[38;2;R;G;Bm`) only when `COLORTERM` is `truecolor`
  or `24bit`; otherwise map to the 256-colour cube (`ESC[38;5;Nm`, N = 16 + 36r + 6g + b with r, g, b in 0..5).
- **Fit the width.** Read `shutil.get_terminal_size((80, 24))` (Python) or `process.stdout.columns` (Node). A banner
  wider than the terminal falls back to plain text; never let a figlet banner wrap.
- **Restore the terminal, always.** Hide the cursor with `ESC[?25l` only inside `try`/`finally`, and handle SIGINT
  and SIGTERM: clear the line, show the cursor (`ESC[?25h`), reset attributes (`ESC[0m`), restore any `termios`
  settings, print "Interrupted." to stderr and exit 130. A second Ctrl-C during cleanup exits immediately.
- **Typing effects are for demos only**, at 20 to 40 ms per character, capped at about 1 s total, and never on
  text the person must act on. Screen readers announce every redraw; a typed line is read one letter at a time.
- **Accessible output.** Meaning is carried by words ("Failed: 3 files"), with symbols and colour as reinforcement.
  Contrast holds on both dark and light terminal themes; test both. Braille spinners are fine visually but noisy for
  screen readers, so the non-motion path prints one "Working..." line instead.
- **No emoji** in professional output. Use plain symbols such as ✓, ✗, → or text.

## Build it
1. Start from `examples/splash.py` (standard library only; gradient banner, 256-colour fallback, skippable reveal,
   transient spinner, SIGINT restore). It is the reference for the rules above; port its `colour_mode()` and
   `motion_allowed()` checks into whatever language the tool uses.
2. Reach for a library only when the tool already depends on it or the effect needs layout. Pin exact versions and
   record them in the lock file; see `references/libraries.md`.
3. Verify all four paths and read the output: `python3 splash.py` in a real terminal, `NO_COLOR=1 python3 splash.py`,
   `python3 splash.py | cat` (non-TTY), and `CI=1 python3 splash.py`. In an agent shell with no TTY, force one with
   `script -q /dev/null python3 splash.py </dev/null | cat -v` and check the escapes: truecolor codes only with
   `COLORTERM=truecolor`, no escapes at all with `NO_COLOR=1`, and a final `ESC[?25h`.
4. Interrupt a running spinner with Ctrl-C and confirm the cursor is back and the prompt is clean.

## Record a demo
Use charmbracelet VHS (https://github.com/charmbracelet/vhs, v0.12.1; `brew install vhs`, which brings `ttyd` and
uses `ffmpeg`). The tape is code, so the demo is reproducible. See `references/vhs.md` for a tape and the checks.
Convert or trim the result with the recipes in `video-toolkit`.

## Sources
- Command Line Interface Guidelines: https://clig.dev (colour, animation, responsiveness, Ctrl-C)
- NO_COLOR convention: https://no-color.org
- Charm tools (Bubble Tea, Lip Gloss, Bubbles, Harmonica, Gum, VHS): https://charm.land
- VHS command reference: https://github.com/charmbracelet/vhs#vhs-command-reference
