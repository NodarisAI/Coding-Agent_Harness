#!/usr/bin/env python3
"""Gradient banner and transient spinner using only the standard library.

Run:  python3 splash.py            (animated when stdout is an interactive terminal)
      NO_COLOR=1 python3 splash.py (plain text, no colour)
      python3 splash.py --no-motion
      python3 splash.py | cat      (non-TTY: plain lines, no animation)
"""
import itertools
import os
import select
import shutil
import signal
import sys
import time

ESC = "\x1b["
HIDE_CURSOR, SHOW_CURSOR = ESC + "?25l", ESC + "?25h"
CLEAR_LINE, RESET = "\r" + ESC + "2K", ESC + "0m"
START, END = (0x7C, 0x3A, 0xED), (0x14, 0xB8, 0xA6)  # purple to teal


def is_tty():
    return sys.stdout.isatty()


def colour_mode():
    """Return 'truecolor', '256' or None, following clig.dev and no-color.org."""
    if "NO_COLOR" in os.environ or "--no-color" in sys.argv:
        return None
    if not is_tty() or os.environ.get("TERM", "") in ("", "dumb"):
        return None
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return "truecolor"
    return "256"


def motion_allowed():
    """Motion only for a person at an interactive terminal who has not opted out."""
    return (
        is_tty()
        and colour_mode() is not None
        and "--no-motion" not in sys.argv
        and not os.environ.get("CI")
        and not os.environ.get("SPLASH_NO_MOTION")
    )


def rgb_to_256(r, g, b):
    return 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)


def paint(text, mode):
    if mode is None:
        return text
    out, n = [], max(len(text) - 1, 1)
    for i, ch in enumerate(text):
        t = i / n
        r, g, b = (round(a + (z - a) * t) for a, z in zip(START, END))
        code = f"38;2;{r};{g};{b}" if mode == "truecolor" else f"38;5;{rgb_to_256(r, g, b)}"
        out.append(f"{ESC}{code}m{ch}")
    return "".join(out) + RESET


def restore_terminal(*_):
    if is_tty():
        sys.stdout.write(CLEAR_LINE + SHOW_CURSOR + RESET)
        sys.stdout.flush()


def on_interrupt(signum, frame):
    restore_terminal()
    sys.stderr.write("Interrupted.\n")
    sys.exit(130)


def key_pressed(timeout):
    """True if the user pressed a key within timeout seconds (skips the splash)."""
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return False
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    return bool(ready)


def banner(title, subtitle):
    mode = colour_mode()
    width = shutil.get_terminal_size((80, 24)).columns
    title = title if len(title) <= width else title[: max(width - 1, 1)] + "…"
    rule = "─" * min(width, max(len(title), len(subtitle)))
    if not motion_allowed():
        print(paint(title, mode))
        print(subtitle[:width])
        return
    sys.stdout.write(HIDE_CURSOR)
    # Reveal the title left to right in about 0.4 s; any key skips to the end.
    step = max(len(title) // 12, 1)
    for i in range(0, len(title) + step, step):
        sys.stdout.write(CLEAR_LINE + paint(title[:i], mode))
        sys.stdout.flush()
        if key_pressed(0.03):
            break
    sys.stdout.write(CLEAR_LINE + paint(title, mode) + "\n" + paint(rule, mode) + "\n")
    print(subtitle[:width])
    sys.stdout.write(SHOW_CURSOR)


def run_with_spinner(label, work, seconds):
    """Show a spinner while work runs; the line is replaced by a result line."""
    if not motion_allowed():
        print(f"{label}...")
        work(seconds)
        print(f"{label}: done")
        return
    frames = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    sys.stdout.write(HIDE_CURSOR)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        sys.stdout.write(f"{CLEAR_LINE}{next(frames)} {label}")
        sys.stdout.flush()
        time.sleep(0.08)
    sys.stdout.write(f"{CLEAR_LINE}{paint('✓', colour_mode())} {label}\n{SHOW_CURSOR}")
    sys.stdout.flush()


def main():
    signal.signal(signal.SIGINT, on_interrupt)
    signal.signal(signal.SIGTERM, on_interrupt)
    try:
        banner("Nodaris command line", "Version 1.0.0. Run with --help for usage.")
        run_with_spinner("Loading configuration", time.sleep, 0.6)
    finally:
        restore_terminal()
    return 0


if __name__ == "__main__":
    sys.exit(main())
