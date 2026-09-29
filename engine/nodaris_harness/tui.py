"""Terminal presentation for the installer and onboarding: colour, a banner, short motion and keyboard menus.

Standard library only, so it runs on a bare machine. Everything degrades to plain text with no escape codes and no
sleeping when the output is not a terminal, NO_COLOR is set, TERM is dumb or NODARIS_REDUCED_MOTION=1. Colour is
24-bit when COLORTERM says truecolor and 256-colour otherwise. The cursor and the terminal mode are restored on
every exit path: normal return, Ctrl-C, an exception and interpreter exit.
"""
import atexit, colorsys, contextlib, os, re, select, shutil, sys, textwrap, threading, time

ACCENT = (45, 212, 191)
MUTED = (140, 150, 160)
WARN = (245, 180, 80)
BAD = (240, 100, 100)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

_SAVED = []          # (fd, termios attributes) to restore
_STATE = {"cursor_hidden": False}


# ---- capability detection --------------------------------------------------------------------------------------

def _out(stream=None):
    return stream or sys.stdout


def reduced_motion():
    return os.environ.get("NODARIS_REDUCED_MOTION", "").strip().lower() in ("1", "true", "yes", "on")


def plain(stream=None):
    """True when output must be plain text: no escape codes and no animation."""
    s = _out(stream)
    if os.environ.get("NO_COLOR", "") != "" or os.environ.get("TERM", "") == "dumb" or reduced_motion():
        return True
    try:
        return not s.isatty()
    except (AttributeError, ValueError):
        return True


def color_mode(stream=None):
    if plain(stream):
        return "none"
    return "truecolor" if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") else "256"


def width(stream=None):
    return max(20, shutil.get_terminal_size((80, 24)).columns)


def unicode_ok(stream=None):
    enc = (getattr(_out(stream), "encoding", None) or "").lower()
    try:
        "█╭─›●○".encode(enc or "ascii")
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def can_prompt():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def visible_len(text):
    return len(ANSI.sub("", text))


def strip(text):
    return ANSI.sub("", text)


# ---- colour ----------------------------------------------------------------------------------------------------

def _rgb_to_256(r, g, b):
    if abs(r - g) < 10 and abs(g - b) < 10:
        if r < 8:
            return 16
        if r > 248:
            return 231
        return 232 + round((r - 8) / 247 * 24)
    return 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)


def fg(rgb, stream=None, mode=None):
    mode = mode or color_mode(stream)
    r, g, b = (int(max(0, min(255, c))) for c in rgb)
    if mode == "truecolor":
        return f"\x1b[38;2;{r};{g};{b}m"
    if mode == "256":
        return f"\x1b[38;5;{_rgb_to_256(r, g, b)}m"
    return ""


def paint(text, rgb, stream=None, bold=False):
    mode = color_mode(stream)
    if mode == "none":
        return text
    return ("\x1b[1m" if bold else "") + fg(rgb, mode=mode) + text + "\x1b[0m"


def bold(text, stream=None):
    return text if plain(stream) else f"\x1b[1m{text}\x1b[0m"


def dim(text, stream=None):
    return text if plain(stream) else f"\x1b[2m{text}\x1b[0m"


def accent(text, stream=None):
    return paint(text, ACCENT, stream)


def hue_rgb(h, s=0.62, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def gradient(text, phase=0.0, stream=None, span=0.85, shimmer=None):
    """Colour each visible character along a smooth hue sweep. shimmer is a column that glows brighter."""
    mode = color_mode(stream)
    if mode == "none":
        return text
    n = max(1, len(text) - 1)
    out, last = [], None
    for i, ch in enumerate(text):
        if ch == " ":
            out.append(ch)
            continue
        r, g, b = hue_rgb(phase + span * i / n)
        if shimmer is not None:
            glow = max(0.0, 1.0 - abs(i - shimmer) / 5.0)
            r, g, b = (int(c + (255 - c) * glow * 0.75) for c in (r, g, b))
        code = fg((r, g, b), mode=mode)
        if code != last:
            out.append(code)
            last = code
        out.append(ch)
    out.append("\x1b[0m")
    return "".join(out)


# ---- terminal state --------------------------------------------------------------------------------------------

def _restore_all():
    for fd, attrs in list(_SAVED):
        try:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
        except Exception:
            pass
    _SAVED.clear()
    if _STATE["cursor_hidden"]:
        try:
            sys.stdout.write("\x1b[0m\x1b[?25h")
            sys.stdout.flush()
        except Exception:
            pass
        _STATE["cursor_hidden"] = False


atexit.register(_restore_all)


@contextlib.contextmanager
def hidden_cursor(stream=None):
    s = _out(stream)
    if plain(s):
        yield
        return
    s.write("\x1b[?25l")
    s.flush()
    _STATE["cursor_hidden"] = True
    try:
        yield
    finally:
        s.write("\x1b[0m\x1b[?25h")
        s.flush()
        _STATE["cursor_hidden"] = False


@contextlib.contextmanager
def cbreak():
    """Read single keys without echo. Ctrl-C still raises KeyboardInterrupt; the mode is always restored."""
    if not can_prompt():
        yield None
        return
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    entry = (fd, old)
    _SAVED.append(entry)
    try:
        tty.setcbreak(fd)
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if entry in _SAVED:
            _SAVED.remove(entry)


def _key_waiting(fd, timeout=0.0):
    if fd is None:
        return False
    try:
        return bool(select.select([fd], [], [], timeout)[0])
    except (OSError, ValueError):
        return False


KEYS = {"[A": "up", "[B": "down", "OA": "up", "OB": "down", "[C": "right", "[D": "left"}


def read_key(fd):
    ch = os.read(fd, 1)
    if ch == b"\x1b":
        seq = b""
        while len(seq) < 5 and _key_waiting(fd, 0.03):
            seq += os.read(fd, 1)
            if seq[-1:].isalpha() or seq[-1:] == b"~":
                break
        return KEYS.get(seq.decode(errors="ignore"), "escape")
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch == b" ":
        return "space"
    if ch == b"\x03":
        raise KeyboardInterrupt
    if ch in (b"\x04", b""):
        raise EOFError
    if ch in (b"k",):
        return "up"
    if ch in (b"j",):
        return "down"
    return ch.decode(errors="ignore")


# ---- banner ----------------------------------------------------------------------------------------------------

GLYPHS = {
    "N": ["██   ██", "███  ██", "██ █ ██", "██  ███", "██   ██"],
    "O": [" █████ ", "██   ██", "██   ██", "██   ██", " █████ "],
    "D": ["██████ ", "██   ██", "██   ██", "██   ██", "██████ "],
    "A": [" █████ ", "██   ██", "███████", "██   ██", "██   ██"],
    "R": ["██████ ", "██   ██", "██████ ", "██  ██ ", "██   ██"],
    "I": ["██████", "  ██  ", "  ██  ", "  ██  ", "██████"],
    "S": [" ██████", "██     ", " █████ ", "     ██", "██████ "],
}
WORD = "NODARIS"
COMPACT = "N O D A R I S"
TAGLINE = "Coding-agent harness"
BANNER_MIN_WIDTH = 60


def banner_lines(cols=None, unicode=True):
    """The block-letter wordmark when it fits in cols, otherwise a one-line compact wordmark. Never wraps a glyph."""
    cols = cols or width()
    rows = [" ".join(GLYPHS[c][r] for c in WORD) for r in range(5)]
    if not unicode or cols < BANNER_MIN_WIDTH or max(len(r) for r in rows) + 2 > cols:
        return [COMPACT]
    return rows


def splash(stream=None, duration=1.2, tagline=TAGLINE):
    """The banner with a moving rainbow shimmer, under two seconds; any key skips it. Plain text when motion is off."""
    s = _out(stream)
    cols = width(s)
    if plain(s):
        s.write(f"{COMPACT}  {tagline}\n\n" if not s.isatty() else "\n".join(banner_lines(cols, unicode_ok(s))) + f"\n{tagline}\n\n")
        s.flush()
        return
    lines = banner_lines(cols, unicode_ok(s))
    pad = " " * max(0, (cols - max(len(x) for x in lines)) // 2) if len(lines) > 1 else "  "
    wide = max(len(x) for x in lines)
    with hidden_cursor(s), cbreak() as fd:
        s.write("\n" * len(lines))
        start, frame = time.time(), 0
        while True:
            t = time.time() - start
            done = t >= duration
            band = None if done else (t / duration) * (wide + 12) - 6
            phase = 0.55 + t * 0.35
            out = [f"\x1b[{len(lines)}F"]
            for line in lines:
                out.append("\x1b[2K" + pad + gradient(line, phase, s, shimmer=band) + "\n")
            s.write("".join(out))
            s.flush()
            if done:
                break
            if _key_waiting(fd, 0.04):
                try:
                    os.read(fd, 32)
                except OSError:
                    pass
                duration = 0
            frame += 1
        s.write(pad + paint(tagline, MUTED, s) + "\n\n")
        s.flush()


# ---- motion ----------------------------------------------------------------------------------------------------

def type_out(text, stream=None, cps=140, max_seconds=0.6, rgb=None):
    """Write text one character at a time, capped at max_seconds. Plain output writes it at once."""
    s = _out(stream)
    if plain(s):
        s.write(text + "\n")
        s.flush()
        return
    delay = min(1.0 / cps, max_seconds / max(1, len(text)))
    code = fg(rgb, s) if rgb else ""
    s.write(code)
    for ch in text:
        s.write(ch)
        s.flush()
        time.sleep(delay)
    s.write(("\x1b[0m" if code else "") + "\n")
    s.flush()


class Spinner:
    """with Spinner("Checking the install"): ...  A plain stream gets one line at the start and one at the end."""
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label, stream=None):
        self.label, self.s, self._stop, self._t, self.result = label, _out(stream), threading.Event(), None, "done"

    def _spin(self):
        i = 0
        frames = self.FRAMES if unicode_ok(self.s) else "|/-\\"
        while not self._stop.wait(0.08):
            self.s.write("\r\x1b[2K" + paint(frames[i % len(frames)], ACCENT, self.s) + " " + self.label)
            self.s.flush()
            i += 1

    def __enter__(self):
        if plain(self.s):
            self.s.write(self.label + "\n")
            self.s.flush()
        else:
            _STATE["cursor_hidden"] = True
            self.s.write("\x1b[?25l")
            self._t = threading.Thread(target=self._spin, daemon=True)
            self._t.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        failed = exc_type is not None
        word = "failed" if failed else self.result
        if self._t:
            self._stop.set()
            self._t.join()
            mark = paint("x" if failed else "•", BAD if failed else ACCENT, self.s)
            self.s.write(f"\r\x1b[2K{mark} {self.label}: {word}\n\x1b[?25h")
            _STATE["cursor_hidden"] = False
        else:
            self.s.write(f"{self.label}: {word}\n")
        self.s.flush()
        return False


def progress_bar(fraction, bar_width=30, unicode=True):
    fraction = max(0.0, min(1.0, fraction))
    full = int(round(fraction * bar_width))
    a, b = ("█", "░") if unicode else ("#", "-")
    return a * full + b * (bar_width - full) + f" {int(round(fraction * 100)):3d}%"


class Progress:
    """Redraws one line in a terminal; a plain stream gets a line only when the work finishes."""

    def __init__(self, label, stream=None):
        self.label, self.s = label, _out(stream)

    def update(self, fraction):
        if plain(self.s):
            return
        bw = max(10, min(40, width(self.s) - len(self.label) - 10))
        bar = progress_bar(fraction, bw, unicode_ok(self.s))
        self.s.write("\r\x1b[2K" + self.label + " " + gradient(bar[:bw], 0.45, self.s) + bar[bw:])
        self.s.flush()

    def done(self, note="done"):
        if plain(self.s):
            self.s.write(f"{self.label}: {note}\n")
        else:
            self.update(1.0)
            self.s.write(f"  {note}\n")
        self.s.flush()


def panel(title, body, stream=None, rgb=ACCENT, max_width=76):
    """A boxed block of text sized to the terminal. body is a string (paragraphs split on blank lines) or a list of lines."""
    s = _out(stream)
    w = max(24, min(max_width, width(s) - 2))
    inner = w - 4
    rows = []
    paras = body if isinstance(body, list) else body.split("\n")
    for p in paras:
        if not strip(p).strip():
            rows.append("")
            continue
        if visible_len(p) <= inner:
            rows.append(p)
        else:
            rows += textwrap.wrap(strip(p), inner, subsequent_indent="  " if strip(p).startswith(("- ", "  ")) else "")
    u = unicode_ok(s)
    tl, tr, bl, br, h, v = ("╭", "╮", "╰", "╯", "─", "│") if u else ("+", "+", "+", "+", "-", "|")
    edge = (lambda x: paint(x, rgb, s))
    t = f" {title} " if title else ""
    top = edge(tl + h) + bold(t, s) + edge(h * max(0, w - 3 - len(t)) + tr)
    lines = [top] + [edge(v) + " " + r + " " * max(0, inner - visible_len(r)) + " " + edge(v) for r in rows]
    lines.append(edge(bl + h * (w - 2) + br))
    s.write("\n".join(lines) + "\n")
    s.flush()


# ---- questions -------------------------------------------------------------------------------------------------

def _norm_options(options):
    out = []
    for o in options:
        if isinstance(o, (tuple, list)):
            out.append((str(o[0]), str(o[1]) if len(o) > 1 else ""))
        elif isinstance(o, dict):
            out.append((o.get("label", ""), o.get("description", "")))
        else:
            out.append((str(o), ""))
    return out


def _read_line(prompt_text, stream=None):
    s = _out(stream)
    s.write(prompt_text)
    s.flush()
    line = sys.stdin.readline()
    if not line:
        s.write("\n")
        raise EOFError
    return line.rstrip("\n")


def _numbered(question, opts, multi, current, stream):
    s = _out(stream)
    s.write(question + "\n")
    for i, (label, desc) in enumerate(opts, 1):
        mark = ("[x] " if i - 1 in current else "[ ] ") if multi else ""
        s.write(f"  {i}. {mark}{label}" + (f": {desc}" if desc else "") + "\n")
    default = ",".join(str(i + 1) for i in sorted(current)) if multi else str(current + 1)
    ask = "Enter the numbers separated by commas" if multi else "Enter a number"
    for _ in range(5):
        try:
            raw = _read_line(f"{ask} [{default}]: ", s).strip()
        except EOFError:
            raw = ""
        if not raw:
            return sorted(current) if multi else current
        try:
            picks = [int(x) - 1 for x in re.split(r"[,\s]+", raw) if x]
        except ValueError:
            s.write("Please enter numbers from the list.\n")
            continue
        if picks and all(0 <= p < len(opts) for p in picks) and (multi or len(picks) == 1):
            return sorted(set(picks)) if multi else picks[0]
        s.write("Please enter numbers from the list.\n")
    return sorted(current) if multi else current


def _menu(question, opts, multi, current, stream):
    s = _out(stream)
    cur = 0 if multi else current
    chosen = set(current) if multi else set()
    u = unicode_ok(s)
    ptr, on, off = ("›", "●", "○") if u else (">", "[x]", "[ ]")
    hint = ("Use the arrow keys to move, Space to select, and Enter to confirm." if multi
            else "Use the arrow keys to move and Enter to confirm. Number keys also work.")
    cols = width(s)
    drawn = 0

    def render():
        nonlocal drawn
        lines = [bold(question, s)]
        for i, (label, desc) in enumerate(opts):
            here = i == cur
            box = (on if i in chosen else off) + " " if multi else ""
            head = f" {ptr if here else ' '} {i + 1}. {box}"
            room = cols - 1 - len(head) - len(label)
            d = ("  " + desc) if desc and room > 8 else ""
            d = d if len(d) <= room else d[:max(0, room - 1)] + ("…" if u else ".")
            text = head + (accent(label, s) if here else label) + dim(d, s)
            lines.append(text)
        lines.append(dim("  " + hint[:cols - 3], s))
        out = (f"\x1b[{drawn}F" if drawn else "") + "\x1b[J" + "\n".join(lines) + "\n"
        s.write(out)
        s.flush()
        drawn = len(lines)

    with hidden_cursor(s), cbreak() as fd:
        render()
        while True:
            k = read_key(fd)
            if k == "up":
                cur = (cur - 1) % len(opts)
            elif k == "down":
                cur = (cur + 1) % len(opts)
            elif k == "space" and multi:
                chosen ^= {cur}
            elif k.isdigit() and 1 <= int(k) <= len(opts):
                cur = int(k) - 1
                if multi:
                    chosen ^= {cur}
                else:
                    break
            elif k == "enter":
                break
            render()
        s.write(f"\x1b[{drawn}F\x1b[J")
        picked = sorted(chosen) if multi else cur
        answer = ", ".join(opts[i][0] for i in picked) if multi else opts[cur][0]
        s.write(bold(question, s) + " " + accent(answer or "None", s) + "\n")
        s.flush()
    return picked


def choose(question, options, default=0, stream=None):
    """Single choice; returns the index. Arrow keys in a terminal, a numbered list otherwise."""
    opts = _norm_options(options)
    default = default if 0 <= default < len(opts) else 0
    if plain(stream) or not can_prompt():
        return _numbered(question, opts, False, default, stream)
    return _menu(question, opts, False, default, stream)


def choose_many(question, options, preselected=(), stream=None):
    """Multiple choice; returns a sorted list of indexes."""
    opts = _norm_options(options)
    pre = [i for i in preselected if 0 <= i < len(opts)]
    if plain(stream) or not can_prompt():
        return _numbered(question, opts, True, pre, stream)
    return _menu(question, opts, True, pre, stream)


def ask(question, default="", stream=None, required=False):
    """A text answer. Enter keeps the default shown in brackets."""
    s = _out(stream)
    suffix = f" [{default}]" if default else ""
    for _ in range(5):
        try:
            raw = _read_line(bold(question, s) + suffix + " ", s).strip()
        except EOFError:
            return default
        if raw or default or not required:
            return raw or default
        s.write("An answer is required.\n")
    return default


def confirm(question, default=True, stream=None):
    s = _out(stream)
    hint = "(Y/n)" if default else "(y/N)"
    if stream is None and can_prompt():
        # One key, no Enter needed; arrow keys and other escape sequences are ignored instead of echoed.
        s.write(bold(question, s) + f" {hint} ")
        s.flush()
        with cbreak() as fd:
            if fd is not None:
                while True:
                    k = read_key(fd)
                    if k in ("y", "Y") or (k == "enter" and default):
                        s.write("Yes\n")
                        s.flush()
                        return True
                    if k in ("n", "N") or (k == "enter" and not default):
                        s.write("No\n")
                        s.flush()
                        return False
        s.write("\n")
    for _ in range(5):
        try:
            raw = _read_line(bold(question, s) + f" {hint} ", s).strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        s.write("Please answer yes or no.\n")
    return default


def line(text="", stream=None):
    s = _out(stream)
    s.write(text + "\n")
    s.flush()
