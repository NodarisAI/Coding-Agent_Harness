"""One fast status line for Claude Code: activity, tokens used this session, burn per minute, budget, agents, last memory.

Claude Code runs the command on every refresh with its statusLine JSON on stdin (session_id, transcript_path, model,
workspace). The parsed state is cached in <harness home>/monitor-cache/<session hash>.json with the byte offset
reached, so each call reads only the lines appended since the last one. The line carries numbers, a memory title and
nothing else: no prompts, no file contents, no command output. NO_COLOR turns colour off.

To use it, add this to Claude Code's settings.json yourself (the harness never edits settings files):

    "statusLine": {"type": "command", "command": "nodaris-harness statusline"}

If nodaris-harness is not on PATH, use the full path to the command instead.
"""
import hashlib, json, os, sys, time

from . import monitor, policy

FRAMES = ("▁▃▂", "▂▅▃", "▃▆▅", "▅▇▆", "▆█▇", "▅▇▅", "▃▆▄", "▂▄▃")
ASCII_FRAMES = (".:.", ":^:", "^*^", "*^*", ":*:", "^:^")


def _cache_path(session):
    return os.path.join(policy.home(), "monitor-cache", hashlib.sha256(str(session).encode()).hexdigest()[:24] + ".json")


def _load(path, transcript):
    try:
        with open(path) as fh:
            c = json.load(fh)
        if c.get("transcript") == transcript and isinstance(c.get("state"), dict):
            return c
    except (OSError, ValueError):
        pass
    return None


def _save(path, data):
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass


def _colour_on():
    return os.environ.get("NO_COLOR", "") == "" and os.environ.get("TERM", "") != "dumb"


def session_stats(transcript, session, max_bytes=64 * 1024 * 1024):
    """(stats, complete) for one session, read incrementally from the cached offset. complete is False while a long
    transcript is still being caught up, max_bytes at a time."""
    cpath = _cache_path(session)
    cached = _load(cpath, transcript)
    stats = monitor.SessionStats(cached["state"] if cached else None)
    tail = monitor.Tail(transcript, cached["offset"] if cached else 0)
    stats.feed(tail.read_new(max_bytes))
    sig = monitor.Tail(monitor.signals_path(session), cached.get("sig", 0) if cached else 0)
    stats.feed_signals(sig.read_new())
    complete = tail.offset >= tail.size() - 1
    if complete:
        stats.resolve_agents(os.path.join(os.path.dirname(transcript), session, "subagents"))
    _save(cpath, {"transcript": transcript, "offset": tail.offset, "sig": sig.offset, "state": stats.state()})
    return stats, complete


def line(payload, now=None, unicode=True, colour=None):
    now = time.time() if now is None else now
    colour = _colour_on() if colour is None else colour
    transcript = payload.get("transcript_path") or ""
    session = payload.get("session_id") or os.path.splitext(os.path.basename(transcript))[0]
    if not transcript or not os.path.isfile(transcript):
        return "nodaris: waiting for a session"
    stats, _ = session_stats(transcript, session)

    tot = stats.totals()
    burn = stats.burn_per_min(now)
    b = monitor.budgets()
    used = monitor.billable(tot) / max(1, b["session"])
    h = monitor.heat(burn)
    frames = FRAMES if unicode else ASCII_FRAMES
    speed = 2 + 8 * h
    glyph = frames[int(now * speed) % len(frames)] if burn > 0 else (frames[0])
    if colour:
        glyph = f"\x1b[38;5;{86 if h > 0.66 else 43 if h > 0.33 else 36}m{glyph}\x1b[0m"   # the teal ramp, lighter as it burns faster
        pct = f"{int(used * 100)}%"
        code = {"ok": 37, "warn": 214, "high": 196}[monitor.level(used)]
        pct = f"\x1b[38;5;{code}m{pct}\x1b[0m"
    else:
        pct = f"{int(used * 100)}%"
    parts = [glyph, f"{monitor.fmt_tokens(tot['used'])} tok", f"{monitor.fmt_tokens(burn)}/min", f"budget {pct}"]
    running = stats.running_agents()
    if running:
        parts.append(f"{running} agent{'s' if running != 1 else ''}")
    if stats.memories:
        m = stats.memories[-1]
        parts.append("mem " + (m if len(m) <= 28 else m[:27] + "~"))
    return "  ".join(parts)


def main(a=None, stdin=None, out=None):
    stdin, out = stdin or sys.stdin, out or sys.stdout
    try:
        payload = json.loads(stdin.read() or "{}")
    except ValueError:
        payload = {}
    try:
        enc = (getattr(out, "encoding", None) or "utf-8").lower()
        "▁█".encode(enc)
        uni = True
    except (LookupError, UnicodeEncodeError):
        uni = False
    try:
        text = line(payload if isinstance(payload, dict) else {}, unicode=uni)
    except Exception:  # noqa: BLE001  a status line must never fail loudly
        text = "nodaris"
    out.write(text + "\n")
    out.flush()
    return 0


def turn_notice(ev, now=None):
    """The one line shown in Claude Code (terminal and desktop app) when a turn ends: this request, this session and
    today, in tokens used. Empty when switched off, when the session is still being read, or on any error."""
    try:
        if ev.get("host") != "claude" or monitor.load_settings().get("turn_summary") is False:
            return ""
        transcript = ev.get("transcript_path") or ""
        if not transcript or not os.path.isfile(transcript):
            return ""
        stats, complete = session_stats(transcript, ev.get("session_id") or "", max_bytes=16 * 1024 * 1024)
        if not complete:
            return ""
        from . import usage
        tot, req = stats.totals(), stats.request()
        fmt = monitor.fmt_tokens
        parts = [f"{fmt(req['used'])} for this request" if req["start"] else "",
                 f"{fmt(tot['used'])} this session (plus {fmt(tot['cache_read'])} cache re-reads)"]
        ledger = usage.refresh(0.25, now=now)
        if ledger.complete:
            today = ledger.summary(now)["today"]
            parts.append(f"{fmt(today['used'])} today across {today['sessions']} session"
                         f"{'s' if today['sessions'] != 1 else ''}")
        return "Token use: " + ", ".join(p for p in parts if p) + "."
    except Exception:  # noqa: BLE001  the notice is informative and never holds up the end of a turn
        return ""

