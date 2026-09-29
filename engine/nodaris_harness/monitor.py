"""The token monitor: a live side panel that shows where an agent session's tokens are going.

`nodaris-harness watch` tails the active session transcript read-only, by byte offset, so each tick parses only the
lines appended since the last one. It never writes to the transcript and never prints file contents, prompts or
command output: only paths, memory titles, short command names and numbers.

Claude Code writes one transcript per session at ~/.claude/projects/<cwd with every non-alphanumeric character
replaced by ->/<session>.jsonl. The monitor reads from it:
  - assistant records: message.usage (input, cache creation, cache read and output tokens), counted once per message id,
    and tool_use blocks (Read, Edit, Write paths; Bash commands shortened to the program; Task and Agent launches);
  - user records: tool_result sizes (characters / 4 as a token estimate) and toolUseResult for Task and Agent
    (totalTokens, totalDurationMs, status, agentType);
  - attachment records: hook_additional_context carrying "Memory (recalled for this task)" (titles only) and
    task_status for background agents.
The harness signals log (<harness home>/signals/) supplies gate stops and learner changes.

Codex writes ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl. Its event_msg records with payload.type "token_count" carry
info.last_token_usage per turn (input_tokens includes cached_input_tokens). Only token totals are read for Codex;
TODO: map Codex function_call records to files touched once their argument shape is stable across versions.

Layout: a flame header whose height, colour (amber to red) and flicker speed follow the burn rate, the totals, a
budget bar (amber at 70 percent, red at 90 percent), then "Where the tokens went", "Subagents", "Memories pulled" and
"Files". Under 70 columns the panels stack in one column. With NO_COLOR, TERM=dumb, NODARIS_REDUCED_MOTION=1 or when
the output is not a terminal, it prints one plain summary and exits, unless --follow is given.
"""
import glob, hashlib, json, math, os, re, shlex, shutil, signal, subprocess, sys, time
from datetime import datetime

from . import policy, tui

BURN_WINDOW = 300
MEMORY_MARK = "Memory (recalled for this task)"
READ_TOOLS = {"Read", "NotebookRead"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
AGENT_TOOLS = {"Task", "Agent"}
# A whole-session estimate of new input + output + subagent tokens (cache re-reads excluded);
# settings.session_budget_tokens overrides it.
SESSION_BUDGETS = {"pro": 3_000_000, "max": 15_000_000, "team": 8_000_000, "api": 5_000_000}
AGENT_BUDGETS = {"pro": 150000, "max": 600000, "team": 400000, "api": 300000}
AMBER = (245, 180, 80)
RED = (235, 70, 50)
GREEN = (45, 212, 191)
# Decorative colour is the Nodaris teal ramp; amber and red are kept only for budget warnings and failed agents.
TEAL_DEEP, TEAL, TEAL_LIGHT = (6, 122, 104), (15, 212, 180), (94, 234, 212)
TITLE_RE = re.compile(r"^\s*[-*]\s+\*\*(.+?)\*\*")
NOTE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
SAFE_WORD = re.compile(r"^[A-Za-z0-9_.:/@+-]{1,24}$")


# ---- locating the session ----------------------------------------------------------------------------------------

def claude_projects_root():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def project_dir(cwd, root=None):
    return os.path.join(root or claude_projects_root(), re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(cwd)))


def find_transcript(cwd=None, session=None, root=None, host="claude"):
    """The newest transcript for this directory, or the one named by session (an id or a path)."""
    if session and os.path.isfile(session):
        return session
    if host == "codex":
        return find_codex_rollout(cwd, session)
    d = project_dir(cwd or os.getcwd(), root)
    if session:
        p = os.path.join(d, session if session.endswith(".jsonl") else session + ".jsonl")
        return p if os.path.isfile(p) else None
    files = glob.glob(os.path.join(d, "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def find_codex_rollout(cwd=None, session=None, root=None):
    root = root or os.path.join(os.path.expanduser("~"), ".codex", "sessions")
    files = sorted(glob.glob(os.path.join(root, "*", "*", "*", "rollout-*.jsonl")), key=os.path.getmtime, reverse=True)
    cwd = os.path.abspath(cwd or os.getcwd())
    for f in files[:60]:
        if session and session not in os.path.basename(f):
            continue
        try:
            with open(f, "rb") as fh:
                head = json.loads(fh.readline() or b"{}")
        except (OSError, ValueError):
            continue
        if session or (head.get("payload") or {}).get("cwd") == cwd:
            return f
    return None


# ---- incremental reading -----------------------------------------------------------------------------------------

class Tail:
    """Reads whole JSON lines appended to a file since the last call. A half-written last line waits for the next call."""

    def __init__(self, path, offset=0):
        self.path, self.offset, self.lines_parsed = path, offset, 0

    def read_new(self, max_bytes=64 * 1024 * 1024):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.offset:           # the file was replaced; start again
            self.offset = 0
        if size == self.offset:
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read(min(max_bytes, size - self.offset))
        end = chunk.rfind(b"\n")
        if end < 0:
            return []
        self.offset += end + 1
        rows = []
        for raw in chunk[:end].split(b"\n"):
            if not raw.strip():
                continue
            self.lines_parsed += 1
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows


# ---- helpers -----------------------------------------------------------------------------------------------------

def _ts(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _text_size(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for b in content:
            if isinstance(b, dict):
                n += len(b.get("text") or "") if b.get("type") == "text" else (1600 if b.get("type") == "image" else 0)
            elif isinstance(b, str):
                n += len(b)
        return n
    return 0


def short_command(cmd):
    """The program and up to two plain words, never arguments that could carry a secret or content."""
    if not isinstance(cmd, str) or not cmd.strip():
        return "shell"
    for seg in re.split(r"&&|\|\||;|\|", cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        while words and "=" in words[0] and not words[0].startswith("-"):
            words = words[1:]       # environment assignments
        if not words or words[0] in ("cd", "export", "set", "source", "."):
            continue
        out = [os.path.basename(words[0])]
        for w in words[1:3]:
            if not SAFE_WORD.match(w) or "/" in w or (len(w) >= 16 and re.search(r"\d", w) and re.search(r"[A-Za-z]", w)):
                break
            out.append(w)
        return " ".join(out)[:40]
    return "shell"


def _tag(text, name):
    m = re.search(r"<%s>(.*?)</%s>" % (name, name), text, re.S)
    return m.group(1).strip() if m else None


def memory_title(text, limit=48):
    """The short name of a recalled lesson: the part before its first colon, never the lesson body."""
    parts = [p.strip() for p in re.split(r":\s+", text.strip().rstrip(":")) if p.strip()]
    if len(parts) > 1 and re.match(r"^\d{4}-\d{2}-\d{2}$", parts[0]):
        parts = parts[1:]
    t = parts[0] if parts else ""
    t = re.split(r"(?<=[a-z0-9])\.\s", t)[0]
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0] + "..."
    return t


def fmt_tokens(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fmt_ms(ms):
    s = int((ms or 0) / 1000)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def short_path(path, cwd=None, limit=48):
    if not path:
        return ""
    p = path
    for base in filter(None, [cwd, os.path.expanduser("~")]):
        if p.startswith(base.rstrip("/") + "/"):
            p = ("~/" if base == os.path.expanduser("~") else "") + p[len(base.rstrip("/")) + 1:]
            break
    return p if len(p) <= limit else "..." + p[-(limit - 3):]


def load_settings():
    try:
        with open(os.path.join(policy.home(), "settings.json")) as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def budgets(settings=None):
    s = load_settings() if settings is None else settings
    plan = s.get("plan") if s.get("plan") in SESSION_BUDGETS else "max"
    agent = s.get("subagent_budget_tokens") if isinstance(s.get("subagent_budget_tokens"), int) else AGENT_BUDGETS[plan]
    sess = s.get("session_budget_tokens") if isinstance(s.get("session_budget_tokens"), int) else SESSION_BUDGETS[plan]
    return {"plan": plan, "session": sess, "agents": agent}


def billable(tot):
    """What the session budget counts: new input, output and subagents. Cache re-reads are cheap and excluded."""
    return tot["new_input"] + tot["output"] + tot["subagents"]


def level(fraction):
    """ok under 70 percent, warn from 70, high from 90."""
    return "high" if fraction >= 0.9 else "warn" if fraction >= 0.7 else "ok"


# ---- the session model ---------------------------------------------------------------------------------------------

class SessionStats:
    """Everything the panel shows, built record by record. State is plain JSON so the status line can cache it."""

    def __init__(self, state=None):
        s = state or {}
        self.msgs = s.get("msgs", {})            # message id -> [ts, input, cache_create, cache_read, output]
        self.order = s.get("order", [])          # message ids in arrival order (for the floor and pruning)
        self.base = s.get("base", [0, 0, 0, 0])  # totals of pruned messages
        self.floor = s.get("floor", 0)
        self.turns = s.get("turns", 0)
        self.first_ts = s.get("first_ts")
        self.last_ts = s.get("last_ts")
        self.pending = s.get("pending", {})      # tool_use id -> [tool, label]
        self.reads = s.get("reads", {})          # path -> estimated tokens
        self.shell = s.get("shell", {})          # short command -> estimated tokens
        self.files = s.get("files", {})          # path -> [mode, ts]
        self.agents = s.get("agents", {})        # tool_use id -> {name, type, status, tokens, ms, ts}
        self.memories = s.get("memories", [])    # titles, newest last
        self.gates = s.get("gates", [])          # rule names
        self.learner = s.get("learner", 0)
        self.host = s.get("host", "claude")

    def state(self, keep=400):
        if len(self.order) > keep:               # fold old messages into the base totals
            for mid in self.order[:-keep]:
                m = self.msgs.pop(mid, None)
                if m:
                    self.base = [a + b for a, b in zip(self.base, m[1:])]
            self.order = self.order[-keep:]
        if len(self.pending) > 200:
            self.pending = dict(list(self.pending.items())[-200:])
        if len(self.files) > 200:
            self.files = dict(sorted(self.files.items(), key=lambda kv: kv[1][1] or 0)[-200:])
        return {k: getattr(self, k) for k in ("msgs", "order", "base", "floor", "turns", "first_ts", "last_ts", "pending",
                                              "reads", "shell", "files", "agents", "memories", "gates", "learner", "host")}

    # -- feeding

    def feed(self, rows):
        for r in rows:
            try:
                if self.host == "codex" or r.get("type") in ("session_meta", "event_msg", "response_item", "turn_context"):
                    self._codex(r)
                else:
                    self._claude(r)
            except Exception:  # noqa: BLE001  a record of an unknown shape must never stop the panel
                continue

    def _usage(self, mid, ts, inp, create, read, out):
        if mid not in self.msgs:
            self.order.append(mid)
            self.turns += 1
            if self.turns == 1:
                self.floor = read or (read + create)
        self.msgs[mid] = [ts, inp, create, read, out]
        if ts:
            self.first_ts = ts if self.first_ts is None else min(self.first_ts, ts)
            self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)

    def _claude(self, r):
        t = r.get("type")
        ts = _ts(r.get("timestamp"))
        if r.get("isSidechain"):
            return                                  # a subagent's own turns are counted from its result
        msg = r.get("message") if isinstance(r.get("message"), dict) else {}
        if t == "assistant":
            u = msg.get("usage")
            if isinstance(u, dict):
                mid = msg.get("id") or r.get("requestId") or r.get("uuid")
                self._usage(mid, ts, u.get("input_tokens") or 0, u.get("cache_creation_input_tokens") or 0,
                            u.get("cache_read_input_tokens") or 0, u.get("output_tokens") or 0)
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    self._tool_use(b, ts)
        elif t == "user":
            content = msg.get("content")
            if isinstance(content, str) and "<task-notification>" in content:
                self._notification(content, ts)
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        self._tool_result(b, r.get("toolUseResult"), ts)
        elif t == "attachment":
            a = r.get("attachment") if isinstance(r.get("attachment"), dict) else {}
            if a.get("type") == "hook_additional_context":
                parts = a.get("content")
                parts = parts if isinstance(parts, list) else [parts]
                for p in parts:
                    if isinstance(p, str) and MEMORY_MARK in p:
                        self._memories(p)
            elif a.get("type") == "queued_command":
                raw = a.get("prompt") if isinstance(a.get("prompt"), str) else json.dumps(a)
                if "<task-notification>" in raw:
                    self._notification(raw.replace("\\n", "\n"), ts)
            elif a.get("type") == "task_status":
                desc, status = a.get("description"), a.get("status")
                for ag in self.agents.values():
                    if ag.get("desc") == desc and ag.get("status") in ("running", "background"):
                        ag["status"] = "running" if status == "running" else (status or ag["status"])

    def _tool_use(self, b, ts):
        name, ti, tid = b.get("name") or "", b.get("input") if isinstance(b.get("input"), dict) else {}, b.get("id")
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if name in READ_TOOLS and path:
            self.pending[tid] = ["read", path]
            self.files[path] = [self.files.get(path, ["read"])[0], ts]
        elif name in WRITE_TOOLS and path:
            self.pending[tid] = ["write", path]
            self.files[path] = ["written", ts]
        elif name == "Bash":
            self.pending[tid] = ["shell", short_command(ti.get("command"))]
        elif name in AGENT_TOOLS:
            desc = str(ti.get("description") or ti.get("subagent_type") or "agent")[:60]
            self.agents[tid] = {"name": desc, "desc": ti.get("description"), "type": str(ti.get("subagent_type") or "")[:40],
                                "status": "running", "tokens": 0, "ms": 0, "ts": ts}

    def _tool_result(self, b, tur, ts):
        tid = b.get("tool_use_id")
        kind = self.pending.pop(tid, None)
        est = _text_size(b.get("content")) // 4
        if kind and kind[0] == "read":
            self.reads[kind[1]] = self.reads.get(kind[1], 0) + est
        elif kind and kind[0] == "shell":
            self.shell[kind[1]] = self.shell.get(kind[1], 0) + est
        ag = self.agents.get(tid)
        if ag is not None and isinstance(tur, dict):
            if tur.get("status") == "async_launched" or tur.get("isAsync"):
                ag["status"] = "background"
            else:
                ag["status"] = str(tur.get("status") or ("error" if b.get("is_error") else "completed"))[:12]
                ag["tokens"] = int(tur.get("totalTokens") or 0)
                ag["ms"] = int(tur.get("totalDurationMs") or 0)
                ag["type"] = str(tur.get("agentType") or ag.get("type") or "")[:40]
                ag["done_ts"] = ts
        elif ag is not None and b.get("is_error"):
            ag["status"] = "error"

    def _notification(self, text, ts):
        """A background agent finished: <task-notification> carries its tool-use id, status and usage numbers."""
        for block in NOTE_RE.findall(text):
            tid = _tag(block, "tool-use-id")
            ag = self.agents.get(tid)
            if ag is None:
                continue
            ag["status"] = (_tag(block, "status") or ag["status"])[:12]
            usage = _tag(block, "usage") or ""
            tok = re.search(r"(?:total_tokens|subagent_tokens)\D{0,4}(\d+)", usage)
            ms = re.search(r"duration_ms\D{0,4}(\d+)", usage)
            if tok:
                ag["tokens"] = int(tok.group(1))
            if ms:
                ag["ms"] = int(ms.group(1))
            if ag["status"] not in ("running", "background"):
                ag["done_ts"] = ts

    def _memories(self, text):
        after = text[text.find(MEMORY_MARK) + len(MEMORY_MARK):]
        for line in after.split("\n")[1:]:
            m = TITLE_RE.match(line)
            if m:
                title = memory_title(m.group(1))
                if title in self.memories:
                    self.memories.remove(title)
                self.memories.append(title)
            elif line.strip() and not line.startswith((" ", "\t")) and not line.lstrip().startswith(("-", "*")):
                break
        self.memories = self.memories[-30:]

    def _codex(self, r):
        self.host = "codex"
        p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        if r.get("type") == "event_msg" and p.get("type") == "token_count":
            u = ((p.get("info") or {}).get("last_token_usage")) or {}
            cached = u.get("cached_input_tokens") or 0
            mid = f"codex-{self.turns}"
            self._usage(mid, _ts(r.get("timestamp")), max(0, (u.get("input_tokens") or 0) - cached),
                        u.get("cache_write_input_tokens") or 0, cached, u.get("output_tokens") or 0)

    def feed_signals(self, rows):
        for r in rows:
            if r.get("kind") == "gate" and r.get("rule"):
                self.gates.append(str(r["rule"])[:40])
                self.gates = self.gates[-20:]
            elif r.get("kind") == "learner" and (self.first_ts is None or (r.get("ts") or 0) >= self.first_ts):
                self.learner += int(r.get("applied") or 0) if not isinstance(r.get("applied"), list) else len(r["applied"])

    # -- derived numbers

    def totals(self):
        inp, create, read, out = self.base
        for m in self.msgs.values():
            inp, create, read, out = inp + m[1], create + m[2], read + m[3], out + m[4]
        agents = sum(a.get("tokens") or 0 for a in self.agents.values())
        return {"cache_read": read, "new_input": inp + create, "output": out, "subagents": agents,
                "total": read + inp + create + out + agents}

    def burn_per_min(self, now=None, window=BURN_WINDOW):
        now = time.time() if now is None else now
        n = 0
        for m in self.msgs.values():
            if m[0] and now - window <= m[0] <= now + 5:
                n += m[1] + m[2] + m[3] + m[4]
        for a in self.agents.values():
            t = a.get("done_ts")
            if t and now - window <= t <= now + 5:
                n += a.get("tokens") or 0
        return n / (window / 60.0)

    def running_agents(self):
        return sum(1 for a in self.agents.values() if a.get("status") in ("running", "background"))

    def where(self, cwd=None, limit=6):
        rows = []
        if self.floor and self.turns:
            rows.append((f"Context floor x {self.turns} turns", min(self.floor * self.turns, self.totals()["cache_read"])))
        rows += [("Read " + short_path(p, cwd, 40), n) for p, n in self.reads.items()]
        rows += [("Agent " + a["name"], a.get("tokens") or 0) for a in self.agents.values()]
        rows += [("Shell " + c, n) for c, n in self.shell.items()]
        rows = [r for r in rows if r[1] > 0]
        return sorted(rows, key=lambda r: -r[1])[:limit]

    def recent_files(self, limit=8):
        items = sorted(self.files.items(), key=lambda kv: kv[1][1] or 0)
        return [(p, v[0]) for p, v in items[-limit:]][::-1]


def signals_path(session_id):
    return os.path.join(policy.home(), "signals", hashlib.sha256(str(session_id).encode()).hexdigest()[:24] + ".jsonl")


# ---- the live source -------------------------------------------------------------------------------------------------

class Source:
    """A transcript, its signals file and the learner's, each tailed incrementally."""

    def __init__(self, path, host="claude"):
        self.path = path
        self.stats = SessionStats({"host": host})
        self.tail = Tail(path) if path else None
        sid = os.path.splitext(os.path.basename(path or ""))[0]
        self.sig = [Tail(signals_path(sid)), Tail(signals_path("learner"))]

    def poll(self):
        if self.tail:
            self.stats.feed(self.tail.read_new())
        for t in self.sig:
            self.stats.feed_signals(t.read_new())
        return self.stats


# ---- drawing -----------------------------------------------------------------------------------------------------

def _mix(a, b, f):
    f = max(0.0, min(1.0, f))
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def heat(burn):
    """0 when idle, 1 at about 200k tokens a minute, on a log scale."""
    if burn <= 0:
        return 0.0
    return max(0.0, min(1.0, math.log10(1 + burn) / math.log10(200_000)))


def flame_rows(h, t, cols=14, unicode=True):
    """The flame as rows of (char, 0..1 intensity). Height, flicker and width follow heat h."""
    height = 1 + int(round(h * 4))
    speed = 2.0 + 10.0 * h
    glyphs = ("·", "'", "^", "(", ")", "▲", "▓", "█") if unicode else (".", "'", "^", "(", ")", "A", "#", "#")
    rows = []
    mid = cols / 2.0
    for y in range(height):                  # y = 0 is the top
        depth = (y + 1) / height
        half = max(0.6, (0.35 + 0.65 * depth) * (1.5 + 4.0 * h))
        row = []
        for x in range(cols):
            d = abs(x + 0.5 - mid) / half
            n = 0.5 + 0.5 * math.sin(t * speed + x * 1.7 + y * 2.3) * math.cos(t * speed * 0.6 - x * 0.9)
            v = (1.0 - d) * (0.55 + 0.45 * depth) + 0.25 * (n - 0.5)
            if v <= 0.05:
                row.append((" ", 0.0))
            else:
                g = glyphs[min(len(glyphs) - 1, int(v * (len(glyphs) - 1) + (2 if depth > 0.6 else 0)))]
                row.append((g, v))
        rows.append(row)
    return rows


def embers(h, t, cols=14, lines=2):
    """A few sparks rising above the flame; more and faster as the burn rises."""
    out = [[" "] * cols for _ in range(lines)]
    count = int(1 + h * 5)
    for i in range(count):
        phase = (t * (0.6 + h * 1.6) + i * 0.37) % 1.0
        y = lines - 1 - int(phase * lines)
        x = int((cols / 2 + math.sin(i * 12.9898 + t * 1.3) * (1 + h * 4)) % cols)
        if 0 <= y < lines:
            out[y][x] = "*" if phase < 0.4 else "." if phase < 0.8 else "'"
    return ["".join(r) for r in out]


class Canvas:
    """Lines built from coloured segments, cut to a visible width."""

    def __init__(self, mode):
        self.mode, self.lines = mode, []

    def seg(self, text, rgb=None, bold=False):
        if self.mode == "none" or (rgb is None and not bold):
            return text
        return ("\x1b[1m" if bold else "") + (tui.fg(rgb, mode=self.mode) if rgb else "") + text + "\x1b[0m"

    def add(self, *segs):
        self.lines.append("".join(segs))


def fit(text, w):
    """Cut a line with colour codes to w visible characters."""
    if tui.visible_len(text) <= w:
        return text
    out, n, i = [], 0, 0
    while i < len(text) and n < w:
        m = tui.ANSI.match(text, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        out.append(text[i])
        n += 1
        i += 1
    return "".join(out) + ("\x1b[0m" if "\x1b[" in text else "")


def pad(text, w):
    text = fit(text, w)
    return text + " " * max(0, w - tui.visible_len(text))


def bar(fraction, w, unicode=True):
    fraction = max(0.0, min(1.0, fraction))
    full = int(round(fraction * w))
    a, b = ("█", "░") if unicode else ("#", "-")
    return a * full + b * (w - full)


def level_rgb(fraction):
    return {"ok": GREEN, "warn": AMBER, "high": RED}[level(fraction)]


def sections(stats, cv, width, cwd=None, unicode=True):
    """The body panels as {title: [lines]}."""
    u = unicode
    where = stats.where(cwd)
    top = max([n for _, n in where] or [1])
    bw = max(4, min(14, width // 4))
    w_lines = []
    for label, n in where:
        w_lines.append(pad(label, max(8, width - bw - 8)) + " " + cv.seg(bar(n / top, bw, u), TEAL) + " " + fmt_tokens(n).rjust(6))
    a_lines = []
    for a in list(stats.agents.values())[-6:][::-1]:
        st = a.get("status") or ""
        rgb = GREEN if st == "completed" else TEAL_LIGHT if st in ("running", "background") else RED
        a_lines.append(pad(a["name"], max(8, width - 26)) + " " + cv.seg(st[:10].ljust(10), rgb) + " "
                       + fmt_tokens(a.get("tokens")).rjust(6) + " " + fmt_ms(a.get("ms")).rjust(6))
    m_lines = [("- " if not u else "• ") + t for t in stats.memories[-6:][::-1]]
    f_lines = [cv.seg(("W " if mode == "written" else "R "), TEAL if mode == "written" else tui.MUTED) + short_path(p, cwd, width - 3)
               for p, mode in stats.recent_files(8)]
    return {"Where the tokens went": w_lines or ["nothing measured yet"],
            "Subagents": a_lines or ["none this session"],
            "Memories pulled": m_lines or ["none recalled yet"],
            "Files": f_lines or ["none touched yet"]}


def render_frame(stats, cols, rows, t=0.0, mode="256", cwd=None, unicode=True, now=None, budget=None, path=None):
    """One full frame of the live panel as a list of lines."""
    cv = Canvas(mode)
    b = budget or budgets()
    tot = stats.totals()
    burn = stats.burn_per_min(now)
    h = heat(burn)
    used = billable(tot) / max(1, b["session"])
    agents_used = tot["subagents"] / max(1, b["agents"])
    fw = 14
    flame = flame_rows(h, t, fw, unicode)
    spark = embers(h, t, fw, 2)
    colr = _mix(TEAL_DEEP, TEAL, h)
    art = [cv.seg(s, _mix(colr, TEAL_LIGHT, 0.5)) for s in spark]
    for row in flame:
        art.append("".join(" " if ch == " " else cv.seg(ch, _mix(colr, TEAL_LIGHT, 0.7 * (1 - v))) for ch, v in row))
    art = ["" for _ in range(7 - len(art))] + art
    bw = max(8, min(30, cols - fw - 26))
    info = [cv.seg("Nodaris token monitor", tui.ACCENT, bold=True) + cv.seg("  q to quit", tui.MUTED),
            f"{fmt_tokens(tot['total'])} tokens this session, burning {fmt_tokens(burn)}/min",
            cv.seg(f"cache re-read {fmt_tokens(tot['cache_read'])}  new input {fmt_tokens(tot['new_input'])}  "
                   f"output {fmt_tokens(tot['output'])}  agents {fmt_tokens(tot['subagents'])}", tui.MUTED),
            "Session " + cv.seg(bar(used, bw, unicode), level_rgb(used)) + f" {int(used * 100):3d}% of {fmt_tokens(b['session'])} ({b['plan']})",
            "Agents  " + cv.seg(bar(agents_used, bw, unicode), level_rgb(agents_used)) + f" {int(agents_used * 100):3d}% of {fmt_tokens(b['agents'])}",
            cv.seg((f"Gate stops: {len(stats.gates)} ({', '.join(stats.gates[-3:])})" if stats.gates else "Gate stops: none")
                   + (f"   learner changes: {stats.learner}" if stats.learner else ""), tui.MUTED),
            cv.seg(short_path(path or "", None, max(20, cols - fw - 4)), tui.MUTED) if path else ""]
    lines = []
    compact = cols < 70
    if compact:
        lines += [a for a in art if tui.visible_len(a.strip()) or a is art[-1]][-4:]
        lines += info[:6]
    else:
        for i in range(max(len(art), len(info))):
            lines.append(pad(art[i] if i < len(art) else "", fw + 2) + (info[i] if i < len(info) else ""))
    secs = sections(stats, cv, cols - 2 if compact else (cols - 3) // 2, cwd, unicode)
    rule = "─" if unicode else "-"
    head = lambda title, w: cv.seg(title + " ", tui.ACCENT, bold=True) + cv.seg(rule * max(0, w - len(title) - 1), tui.MUTED)
    if compact:
        for title, body in secs.items():
            lines.append(head(title, cols - 1))
            lines += body[:6]
    else:
        half = (cols - 3) // 2
        for left, right in (("Where the tokens went", "Memories pulled"), ("Subagents", "Files")):
            lines.append(pad(head(left, half), half) + "   " + head(right, half))
            lb, rb = secs[left], secs[right]
            for i in range(max(len(lb), len(rb))):
                lines.append(pad(lb[i] if i < len(lb) else "", half) + "   " + (rb[i] if i < len(rb) else ""))
    return [fit(l, cols) for l in lines[:rows]]


def render_plain(stats, cwd=None, now=None, budget=None, path=None):
    """One summary block with no escape codes, for pipes, NO_COLOR and reduced motion."""
    b = budget or budgets()
    tot = stats.totals()
    burn = stats.burn_per_min(now)
    used = billable(tot) / max(1, b["session"])
    out = ["Nodaris token monitor" + (f" ({short_path(path, None, 60)})" if path else ""),
           f"Tokens this session: {tot['total']:,} (cache re-read {tot['cache_read']:,}, new input {tot['new_input']:,}, "
           f"output {tot['output']:,}, subagents {tot['subagents']:,})",
           f"Burn rate: {int(burn):,} tokens per minute over the last 5 minutes",
           f"Session budget: {int(used * 100)}% of {b['session']:,} new input, output and subagent tokens ({b['plan']} plan, {level(used)})",
           f"Agent budget: {int(tot['subagents'] / max(1, b['agents']) * 100)}% of {b['agents']:,}",
           f"Gate stops: {len(stats.gates)}" + (f"; learner changes: {stats.learner}" if stats.learner else "")]
    for title, body in sections(stats, Canvas("none"), 72, cwd, unicode=False).items():
        out += ["", title + ":"] + ["  " + l.rstrip() for l in body]
    return "\n".join(out)


# ---- commands --------------------------------------------------------------------------------------------------------

def cli_command():
    exe = shutil.which("nodaris-harness")
    return shlex.quote(exe) if exe else f"{shlex.quote(sys.executable)} -m nodaris_harness"


def split(a, out=None):
    """Open the panel beside the agent: a tmux pane, a new macOS terminal tab, or one sentence on how."""
    out = out or sys.stdout
    cmd = cli_command() + " watch" + (f" --session {shlex.quote(a.session)}" if getattr(a, "session", None) else "")
    if os.environ.get("TMUX") and shutil.which("tmux"):
        r = subprocess.run(["tmux", "split-window", "-h", "-l", "38%", cmd], check=False)
        if r.returncode == 0:
            out.write("The token monitor is open in the pane on the right.\n")
            return 0
    prog = os.environ.get("TERM_PROGRAM", "")
    if sys.platform == "darwin" and prog in ("Apple_Terminal", "iTerm.app"):
        if getattr(a, "open", False):
            script = os.path.join(policy.home(), "watch.command")
            os.makedirs(policy.home(), exist_ok=True)
            with open(script, "w") as fh:
                fh.write(f"#!/bin/sh\ncd {shlex.quote(os.getcwd())} && exec {cmd}\n")
            os.chmod(script, 0o700)
            subprocess.run(["open", "-a", "iTerm" if prog == "iTerm.app" else "Terminal", script], check=False)
            out.write("The token monitor is opening in a new terminal window.\n")
            return 0
        out.write(f"Open a new tab in this directory and run: {cmd}\n(Add --open to have it opened for you.)\n")
        return 0
    out.write(f"Side panes need tmux, so run this in a second terminal beside your agent: {cmd}\n")
    return 0


def watch(a, out=None):
    out = out or sys.stdout
    if getattr(a, "split", False):
        return split(a, out)
    host = getattr(a, "host", None) or "claude"
    cwd = os.getcwd()
    path = find_transcript(cwd, getattr(a, "session", None), host=host)
    follow = getattr(a, "follow", False)
    if tui.plain(out):
        return _watch_plain(path, host, cwd, follow, out)
    return _watch_live(path, host, cwd, getattr(a, "session", None), out)


def _watch_plain(path, host, cwd, follow, out):
    if not path and not follow:
        out.write("No agent session was found for this directory. Start the agent here, or pass --session.\n")
        return 1
    src = Source(path, host)
    last = None
    try:
        while True:
            if src.path:
                text = render_plain(src.poll(), cwd, path=src.path)
                if text != last:
                    out.write(text + "\n" + ("\n" if follow else ""))
                    out.flush()
                    last = text
            if not follow:
                return 0
            time.sleep(2.0)
            if not src.path:
                p = find_transcript(cwd, host=host)
                if p:
                    src = Source(p, host)
    except KeyboardInterrupt:
        return 0


def _watch_live(path, host, cwd, session, out):
    src = Source(path, host)
    mode = tui.color_mode(out)
    uni = tui.unicode_ok(out)
    fps = 4.0
    prev_term = signal.getsignal(signal.SIGTERM)

    def _term(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)
    out.write("\x1b[?1049h\x1b[?25l")
    out.flush()
    tui._STATE["cursor_hidden"] = True
    t0, last_look = time.time(), 0.0
    try:
        with tui.cbreak() as fd:
            while True:
                now = time.time()
                if not session and now - last_look > 5:     # follow a newer session in this directory
                    last_look = now
                    p = find_transcript(cwd, host=host)
                    if p and p != src.path:
                        src = Source(p, host)
                src.poll()
                size = shutil.get_terminal_size((80, 24))
                if src.path:
                    lines = render_frame(src.stats, max(30, size.columns - 1), max(8, size.lines - 1), now - t0, mode, cwd, uni, now, path=src.path)
                else:
                    lines = ["Nodaris token monitor", "", "Waiting for an agent session in this directory.", "Press q to quit."]
                out.write("\x1b[H" + "\n".join(l + "\x1b[0m\x1b[K" for l in lines) + "\x1b[J")
                out.flush()
                if fd is not None and tui._key_waiting(fd, 1.0 / fps):
                    if tui.read_key(fd) in ("q", "Q", "escape"):
                        break
                elif fd is None:
                    time.sleep(1.0 / fps)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        out.flush()
        tui._STATE["cursor_hidden"] = False
        signal.signal(signal.SIGTERM, prev_term)
    return 0


def add_arguments(p):
    p.add_argument("--session", help="a session id or transcript path; the newest for this directory by default")
    p.add_argument("--host", choices=["claude", "codex"], default="claude")
    p.add_argument("--follow", action="store_true", help="in plain mode, keep printing a summary when it changes")
    p.add_argument("--split", action="store_true", help="open the panel in a side pane (tmux) or show how")
    p.add_argument("--open", action="store_true", help="with --split on macOS, open a new terminal window for it")
