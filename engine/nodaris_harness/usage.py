"""Token use across sessions: a small ledger built from every Claude Code transcript on this machine.

Claude Code writes one transcript per session (~/.claude/projects/<dir>/<session>.jsonl) and one per subagent
(<dir>/<session>/subagents/agent-<id>.jsonl). Each assistant record carries message.usage. The ledger reads only
records that contain "usage", remembers the byte offset reached in every file, and adds each API message once to the
day it happened on (local time), so a later update parses only what was appended.

Definitions, shared with the live panel and the end-of-turn line:
  - tokens used: new input, cache writes and output. This is the work done for you.
  - cache re-reads: the conversation re-sent from the prompt cache on every turn. Billed at about a tenth of the input
    price, so it is shown separately and never added to "tokens used".

Each message is counted once even when it is logged on several lines or copied into another file (a resumed or forked
session), by a short hash of its message id kept for the retention window. The ledger holds numbers, session ids and
message-id hashes only, never text. It is rebuilt from the transcripts if deleted.
"""
import glob, hashlib, json, os, subprocess, time
from datetime import datetime, timedelta

from . import policy

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

KEEP_DAYS = 8
VERSION = 2
BLOCK = 16 * 1024 * 1024


def ledger_path():
    return os.path.join(policy.home(), "usage", "ledger.json")


def projects_root():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def day_of(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _ts(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def session_of(path):
    """The session a transcript belongs to: its own id, or for a subagent the id of the session that launched it."""
    parts = os.path.normpath(path).split(os.sep)
    if len(parts) >= 3 and parts[-2] == "subagents":
        return parts[-3]
    return os.path.splitext(parts[-1])[0]


def transcripts(root=None, since=0.0):
    """(path, size) of every main and subagent transcript modified since `since`, newest first."""
    root = root or projects_root()
    found = []
    for pattern in ("*/*.jsonl", "*/*/subagents/*.jsonl"):
        for p in glob.glob(os.path.join(root, pattern)):
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime >= since:
                found.append((st.st_mtime, p, st.st_size))
    return [(p, size) for _, p, size in sorted(found, reverse=True)]


def usage_of(row):
    """(message id, timestamp, [input, cache writes, cache reads, output]) of an assistant record, or None."""
    msg = row.get("message") if isinstance(row, dict) else None
    if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
        return None
    u = msg["usage"]
    vals = [int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                                         "output_tokens")]
    return msg.get("id") or row.get("requestId") or row.get("uuid"), _ts(row.get("timestamp")), vals


def used(vals):
    """Tokens used: new input, cache writes and output. Cache re-reads are reported separately."""
    return vals[0] + vals[1] + vals[3]


def _hash(mid):
    return hashlib.sha256(str(mid).encode()).hexdigest()[:12]


class Ledger:
    def __init__(self, data=None):
        d = data if isinstance(data, dict) and data.get("v") == VERSION else {}
        self.files = d.get("files", {})     # path -> {"off": bytes read, "mid": last message id, "last": [day, 4 values]}
        self.days = d.get("days", {})       # day -> {"t": [input, cache writes, cache reads, output], "s": [session ids]}
        self.ids = d.get("ids", {})         # day -> [message id hashes]
        self.complete = bool(d.get("complete"))
        self._seen = {h for hs in self.ids.values() for h in hs}

    @classmethod
    def load(cls, path=None):
        try:
            with open(path or ledger_path()) as fh:
                return cls(json.load(fh))
        except (OSError, ValueError):
            return cls()

    def save(self, path=None):
        path = path or ledger_path()
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                json.dump({"v": VERSION, "files": self.files, "days": self.days, "ids": self.ids,
                           "complete": self.complete}, fh, separators=(",", ":"))
            os.replace(tmp, path)
        except OSError:
            pass

    # -- counting

    def _add(self, day, vals, sign=1):
        d = self.days.setdefault(day, {"t": [0, 0, 0, 0], "s": []})
        d["t"] = [a + sign * b for a, b in zip(d["t"], vals)]

    def _record(self, entry, session, mid, ts, vals, cutoff):
        if ts is None or ts < cutoff:
            return
        day = day_of(ts)
        if mid is not None and mid == entry.get("mid") and entry.get("last"):
            old = entry["last"]                   # the same message logged again: replace, never add twice
            self._add(old[0], old[1:], -1)
            self._add(day, vals)
            entry["last"] = [day] + vals
            return
        h = _hash(mid)
        if h in self._seen:
            return                                # already counted, here or in a copied session
        self._seen.add(h)
        self.ids.setdefault(day, []).append(h)
        self._add(day, vals)
        sessions = self.days[day]["s"]
        if session not in sessions:
            sessions.append(session)
        entry["mid"], entry["last"] = mid, [day] + vals

    def _read_file(self, path, size, cutoff, deadline):
        entry = self.files.setdefault(path, {"off": 0})
        if size < entry.get("off", 0):            # the file was replaced
            entry.update({"off": 0, "mid": None, "last": None})
        session = session_of(path)
        try:
            fh = open(path, "rb")
        except OSError:
            return True
        with fh:
            while entry["off"] < size:
                fh.seek(entry["off"])
                chunk = fh.read(min(BLOCK, size - entry["off"]))
                end = chunk.rfind(b"\n")
                if end < 0:
                    break
                for raw in chunk[:end].split(b"\n"):
                    if b'"usage"' not in raw:
                        continue
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    got = usage_of(row)
                    if got:
                        self._record(entry, session, got[0], got[1], got[2], cutoff)
                entry["off"] += end + 1
                if deadline is not None and time.time() > deadline:
                    return entry["off"] >= size
        return True

    def update(self, root=None, deadline=None, now=None):
        """Read what was appended to every transcript of the retention window. False when the deadline cut it short."""
        now = time.time() if now is None else now
        cutoff_day = day_of(now - (KEEP_DAYS - 1) * 86400)
        cutoff = datetime.strptime(cutoff_day, "%Y-%m-%d").timestamp()
        done = True
        live = set()
        for path, size in transcripts(root, cutoff):
            live.add(path)
            if deadline is not None and time.time() > deadline:
                done = False
                continue
            if size > self.files.get(path, {}).get("off", 0) or path not in self.files:
                done = self._read_file(path, size, cutoff, deadline) and done
        for path in [p for p in self.files if p not in live]:
            del self.files[path]                  # untouched for the whole window; a later append re-reads safely
        for day in [d for d in self.days if d < cutoff_day]:
            del self.days[day]
        for day in [d for d in self.ids if d < cutoff_day]:
            self._seen.difference_update(self.ids.pop(day))
        self.complete = done
        return done

    # -- reading

    def totals(self, days):
        t, sessions = [0, 0, 0, 0], set()
        for day in days:
            d = self.days.get(day)
            if d:
                t = [a + b for a, b in zip(t, d["t"])]
                sessions.update(d["s"])
        return {"used": used(t), "cache_read": t[2], "output": t[3], "sessions": len(sessions)}

    def summary(self, now=None):
        now = time.time() if now is None else now
        today = day_of(now)
        week = [(datetime.fromtimestamp(now) - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        return {"today": self.totals([today]), "week": self.totals(week), "complete": self.complete,
                "by_day": [(d, self.totals([d])) for d in reversed(week)]}


def _lock(path):
    """A non-blocking lock so two updaters never race; None when another process holds it."""
    if fcntl is None:
        return True
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fh = open(path + ".lock", "a")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        return None


def refresh(budget_s=None, root=None, now=None):
    """Update the ledger within budget_s seconds (all of it when None) and return it. Never raises."""
    path = ledger_path()
    lock = _lock(path)
    ledger = Ledger.load(path)
    if not lock:
        return ledger                              # someone else is updating; their result lands in a moment
    try:
        ledger.update(root, None if budget_s is None else time.time() + budget_s, now)
        ledger.save(path)
    except Exception:  # noqa: BLE001  usage figures are informative; they never break a hook
        pass
    finally:
        if lock is not True:
            lock.close()
    return ledger


def start_in_background(cli):
    """Called at session start: bring the ledger up to date in a detached process, so the first turn stays fast."""
    if os.environ.get("NODARIS_HARNESS_NO_BG"):
        return False
    try:
        subprocess.Popen([cli, "usage", "--update", "--quiet"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def fmt(n):
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def report(ledger, now=None):
    """The `usage` command's text: today, the last seven days and one line per day."""
    s = ledger.summary(now)
    lines = [f"Today: {fmt(s['today']['used'])} tokens used across {s['today']['sessions']} session(s), "
             f"plus {fmt(s['today']['cache_read'])} cache re-reads.",
             f"Last 7 days: {fmt(s['week']['used'])} tokens used across {s['week']['sessions']} session(s), "
             f"plus {fmt(s['week']['cache_read'])} cache re-reads.", ""]
    for day, t in s["by_day"]:
        lines.append(f"  {day}  {fmt(t['used']).rjust(7)} used  {fmt(t['cache_read']).rjust(7)} re-read  "
                     f"{t['sessions']:>3} session(s)")
    lines += ["", "Tokens used are new input, cache writes and output, subagents included. Cache re-reads are the "
                  "conversation re-sent on each turn and cost about a tenth as much."]
    if not s["complete"]:
        lines.append("Still counting older sessions; run again in a minute for complete figures.")
    return "\n".join(lines)


def main(a, out=None):
    import sys
    out = out or sys.stdout
    ledger = refresh(None)
    if not getattr(a, "quiet", False):
        out.write(report(ledger) + "\n")
    return 0
