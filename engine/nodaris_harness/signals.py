"""Signals: small, redacted observations of how this person works with the agent, captured by the existing hooks.

Kinds: correction (the person pushed back on the last answer), unrouted (a request no playbook matched), skill (a
skill the agent opened), gate (a check sent the agent back or refused a call). Text is redacted before it is
written and cut to a short excerpt; nothing here is ever sent anywhere by this module.
"""
import glob, hashlib, json, os, re, time

from . import policy, redact

CORRECTION = re.compile(r"^\s*(no\b|nope|don'?t\b|do not\b|stop\b|actually\b|instead\b|wrong\b|that'?s (wrong|not)|"
                        r"i (said|told you|asked)|not what i|why did you|you (didn'?t|forgot|missed|should)|again[,.]|"
                        r"shorter|too long|less (words|text)|more detail|explain more|too (verbose|wordy))", re.I)
EXCERPT = 300


def _dir():
    d = os.path.join(policy.home(), "signals")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _path(session):
    return os.path.join(_dir(), hashlib.sha256(str(session).encode()).hexdigest()[:24] + ".jsonl")


def record(session, kind, text=None, **data):
    """Append one signal. Never raises: a signal that cannot be written must not disturb the agent's work."""
    try:
        row = {"ts": round(time.time(), 3), "session": hashlib.sha256(str(session).encode()).hexdigest()[:12],
               "kind": kind, **data}
        if text is not None:
            r = redact.redact_text(text[:EXCERPT])
            if r.verdict == "refused":
                return
            row["text"] = r.text
        with open(_path(session), "a") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001
        pass


def is_correction(prompt):
    return bool(CORRECTION.search(prompt or ""))


def load(since=0.0):
    rows = []
    for path in glob.glob(os.path.join(_dir(), "*.jsonl")):
        if os.path.getmtime(path) < since:
            continue
        with open(path) as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("ts", 0) >= since:
                    rows.append(row)
    return sorted(rows, key=lambda r: r["ts"])
