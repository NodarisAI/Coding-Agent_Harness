"""The token monitor: a live side panel that shows what one agent session is doing and what it costs, as it happens.

`nodaris-harness watch` tails the session transcript read-only, by byte offset, so each tick parses only the lines
appended since the last one. It never writes to the transcript and never prints file contents, prompts or command
output: only paths, memory titles, short command names and numbers.

Which session: `nodaris` starts Claude Code with NODARIS_PANEL_LINK set; the harness's SessionStart hook writes the
session's transcript path to <harness home>/live/<link>.json, and the panel started with `--link` follows exactly that
session through /clear, /resume and compaction. In the desktop app the hook writes the same kind of link, keyed by the
app's own session id (CLAUDE_CODE_HOST_SESSION_ID), and the dashboard the session offers to open follows that link.
Without a link, --session names one (found in any project folder), then the session whose shell started the panel
(CLAUDE_CODE_SESSION_ID); only when none of these exists is the newest transcript for this directory used, and the
panel then stays on it. It never jumps to another session that happens to write later (Varun, 2026-09-30: every
session runs in ai-os, so "newest in this folder" showed whichever session was busiest, not the one in front of him).

Claude Code writes one transcript per session at ~/.claude/projects/<cwd with every non-alphanumeric character
replaced by ->/<session>.jsonl, and one per subagent at <that dir>/<session>/subagents/agent-<id>.jsonl with an
agent-<id>.meta.json naming the tool call that launched it. The monitor reads:
  - assistant records: message.usage, counted once per message id; tool_use blocks (paths, a shortened command, agent
    launches); message.model and stop_reason;
  - user records: the person's prompts (only their timestamps), tool_result sizes (characters / 4 as an estimate) and
    toolUseResult for Task and Agent (status, agentId, totalTokens as a fallback);
  - attachment records: recalled memories and lessons (titles only) and task_status for background agents;
  - system records: turn_duration, which marks the end of a turn;
  - each subagent's own transcript, live, so its tokens and its current tool show while it runs.

Numbers follow usage.py: "tokens used" are new input, cache writes and output (subagents included); cache re-reads
are shown separately because they cost about a tenth as much.

"This session" counts from the moment the session was last opened: Claude Code records every SessionStart hook in the
transcript as an attachment named "SessionStart:<source>", and a startup, resume or clear opens the session while a
compaction does not. A conversation resumed over several days shows its whole total on a second, labelled line.
Other sessions appear only in their own block, from the ledger in usage.py.
"""
import argparse, glob, hashlib, json, math, os, re, shlex, shutil, signal, subprocess, sys, threading, time
from datetime import datetime

from . import policy, tui, usage

BURN_WINDOW = 300
FLOW_SPAN = 600
MEMORY_MARK = "Memory (recalled for this task)"
LESSON_MARK = "Lessons recorded from earlier work that match this request:"
FILE_LESSON_RE = re.compile(r"^Lessons recorded for (.+):\s*$")
REPO_CARD_RE = re.compile(r"^#+\s*Repo card:")
READ_TOOLS = {"Read", "NotebookRead"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
AGENT_TOOLS = {"Task", "Agent"}
# Tokens used per session (new input, cache writes, output, subagents); settings.session_budget_tokens overrides it.
SESSION_BUDGETS = {"pro": 3_000_000, "max": 15_000_000, "team": 8_000_000, "api": 5_000_000}
AGENT_BUDGETS = {"pro": 150000, "max": 600000, "team": 400000, "api": 300000}
AMBER = (245, 180, 80)
RED = (235, 70, 50)
GREEN = (45, 212, 191)
# Decorative colour is the Nodaris teal ramp; amber and red are kept only for budget warnings and failed agents.
TEAL_DEEP, TEAL, TEAL_LIGHT = (6, 122, 104), (15, 212, 180), (94, 234, 212)
TITLE_RE = re.compile(r"^\s*[-*]\s+(?:Lesson:\s+)?\*\*(.+?)\*\*")
LESSON_LINE_RE = re.compile(r"^\s*-\s+When (.+?):\s")
MATCHED_RE = re.compile(r"\(matched: ([^)]{1,80})\)\s*$")
NOTE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
SAFE_WORD = re.compile(r"^[A-Za-z0-9_.:/@+-]{1,24}$")
LINK_RE = re.compile(r"^[a-f0-9]{8,32}$")
NOT_A_PROMPT = ("<task-notification>", "<local-command", "<command-name>", "<command-message>", "<system-reminder>",
                "Caveat:", "<bash-", "[Request interrupted")
OPEN_SOURCES = ("startup", "resume", "clear")
# Lessons and memory written during the session: the harness and memory-loop commands, and the auto-memory and handoff
# files Claude Code sessions write. A match only means work was saved; nothing about its content is read.
SAVE_COMMAND_RE = re.compile(r"(?:nodaris-harness|memory-loop)\S*\s+(?:lessons\s+)?add\b")
SAVE_PATH_RE = re.compile(r"/\.claude/projects/[^/]+/memory/[^/]+\.md$|/\.remember/[^/]+\.md$")
CACHE_WARN = 900           # seconds before the prompt cache expires when the panel starts to say so
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
BLOCKS = " ▁▂▃▄▅▆▇█"


# ---- locating the session ----------------------------------------------------------------------------------------

def claude_projects_root():
    return usage.projects_root()


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
        name = session if session.endswith(".jsonl") else session + ".jsonl"
        if os.path.basename(name) != name:
            return None
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
        found = glob.glob(os.path.join(glob.escape(root or claude_projects_root()), "*", glob.escape(name)))
        return max(found, key=os.path.getmtime) if found else None
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


def session_link(env=None):
    """The link this Claude Code process writes its transcript under: the `nodaris` launcher's, or one derived from
    the desktop app's session id. None for a plain command-line session, which the panel finds by --session."""
    env = os.environ if env is None else env
    if env.get("NODARIS_PANEL_LINK"):
        return env["NODARIS_PANEL_LINK"]
    host_id = env.get("CLAUDE_CODE_HOST_SESSION_ID")
    return hashlib.sha256(host_id.encode()).hexdigest()[:32] if host_id else None


def resolve(a, cwd, env=None):
    """(transcript path, session, link) for a panel: an explicit --link or --session, else the session whose shell
    started it, else the newest in this directory."""
    env = os.environ if env is None else env
    host = getattr(a, "host", None) or "claude"
    link, session = getattr(a, "link", None), getattr(a, "session", None)
    if not link and not session and host == "claude":
        link = session_link(env) if env.get("CLAUDE_CODE_HOST_SESSION_ID") and not env.get("NODARIS_PANEL_LINK") else None
        session = env.get("CLAUDE_CODE_SESSION_ID") or None
    path = (read_link(link) if link else None) or find_transcript(cwd, session, host=host)
    return path, session, link


def next_path(current, link, cwd, host="claude"):
    """Which transcript the live panel shows next tick: the link's (it moves with /clear and /resume), otherwise the
    one it already shows. Only a panel that has found nothing yet looks for the newest in this directory."""
    if link:
        return read_link(link) or current
    return current or find_transcript(cwd, host=host)


def link_path(link):
    return os.path.join(policy.home(), "live", link + ".json")


def write_link(link, transcript, session, cwd):
    """Called by the SessionStart hook: tell the panel started with --link which transcript this session writes."""
    if not (isinstance(link, str) and LINK_RE.match(link)) or not transcript:
        return False
    path = link_path(link)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"transcript": transcript, "session": session, "cwd": cwd, "ts": time.time()}, fh)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def read_link(link):
    try:
        with open(link_path(link)) as fh:
            d = json.load(fh)
        return d.get("transcript") if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ---- incremental reading -----------------------------------------------------------------------------------------

class Tail:
    """Reads whole JSON lines appended to a file since the last call. A half-written last line waits for the next call."""

    def __init__(self, path, offset=0):
        self.path, self.offset, self.lines_parsed = path, offset, 0

    def size(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

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

_ts = usage._ts


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


def tool_label(name, ti):
    """What a tool call is doing, in a few words: the tool and a file name or a short command, never content."""
    ti = ti if isinstance(ti, dict) else {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if name == "Bash":
        return "Running " + short_command(ti.get("command"))
    if name in READ_TOOLS and path:
        return "Reading " + os.path.basename(path)
    if name in WRITE_TOOLS and path:
        return ("Writing " if name == "Write" else "Editing ") + os.path.basename(path)
    if name in AGENT_TOOLS:
        return "Starting a subagent"
    if name in ("Grep", "Glob"):
        return "Searching the code"
    if name in ("WebFetch", "WebSearch"):
        return "Reading the web"
    short = name.split("__")[-1] if "__" in name else name
    return "Using " + (short[:24] or "a tool")


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
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fmt_ms(ms):
    s = int((ms or 0) / 1000)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
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
    agent += int(s.get("subagent_budget_extra") or 0) if isinstance(s.get("subagent_budget_extra"), int) else 0
    sess = s.get("session_budget_tokens") if isinstance(s.get("session_budget_tokens"), int) else SESSION_BUDGETS[plan]
    return {"plan": plan, "session": sess, "agents": agent}


def billable(tot):
    """Tokens used: new input (with cache writes), output and subagents. Cache re-reads are cheap and excluded."""
    return tot["new_input"] + tot["output"] + tot["subagents"]


def level(fraction):
    """ok under 70 percent, warn from 70, high from 90."""
    return "high" if fraction >= 0.9 else "warn" if fraction >= 0.7 else "ok"


def agent_usage(path):
    """[input, cache writes, cache reads, output] of a subagent transcript, each API message once."""
    per_id = {}
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if b'"usage"' not in raw:
                    continue
                try:
                    got = usage.usage_of(json.loads(raw))
                except ValueError:
                    continue
                if got:
                    per_id[got[0]] = got[2]
    except OSError:
        return None
    t = [0, 0, 0, 0]
    for v in per_id.values():
        t = [a + b for a, b in zip(t, v)]
    return t


def human_prompt(r, msg):
    """True for a message the person typed, not a tool result, a command echo or a background notice."""
    if r.get("isMeta") or r.get("isSidechain") or r.get("isCompactSummary") or r.get("toolUseResult") is not None:
        return False
    c = msg.get("content")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return False
        c = " ".join(b.get("text") or "" for b in c if isinstance(b, dict) and b.get("type") == "text")
    return isinstance(c, str) and bool(c.strip()) and not c.lstrip().startswith(NOT_A_PROMPT)


# ---- the session model ---------------------------------------------------------------------------------------------

class SessionStats:
    """Everything the panel shows, built record by record. State is plain JSON so the status line can cache it."""

    FIELDS = ("msgs", "order", "base", "floor", "turns", "first_ts", "last_ts", "pending", "reads", "shell", "files",
              "agents", "memories", "recalls", "gates", "learner", "host", "title", "entry", "model", "context",
              "activity", "prompts", "agent_events", "opens", "base_open", "ttl", "saved")

    def __init__(self, state=None):
        s = state or {}
        self.msgs = s.get("msgs", {})            # message id -> [ts, input, cache_create, cache_read, output]
        self.order = s.get("order", [])          # message ids in arrival order (for pruning)
        self.base = s.get("base", [0, 0, 0, 0])  # totals of pruned messages
        self.floor = s.get("floor", 0)
        self.turns = s.get("turns", 0)
        self.first_ts = s.get("first_ts")
        self.last_ts = s.get("last_ts")
        self.pending = s.get("pending", {})      # tool_use id -> [kind, label]
        self.reads = s.get("reads", {})          # path -> estimated tokens
        self.shell = s.get("shell", {})          # short command -> estimated tokens
        self.files = s.get("files", {})          # path -> [mode, ts]
        self.agents = s.get("agents", {})        # tool_use id -> {name, type, status, tokens, used, cache, ms, ts, ...}
        self.memories = s.get("memories", [])    # memory titles, newest last
        self.recalls = s.get("recalls", [])      # [{"ts": prompt time, "items": [[kind, title, why]]}], newest last
        self.gates = s.get("gates", [])          # rule names
        self.learner = s.get("learner", 0)
        self.host = s.get("host", "claude")
        self.title = s.get("title", "")
        self.entry = s.get("entry", "")
        self.model = s.get("model", "")
        self.context = s.get("context", 0)       # input + cache of the latest main-thread call: the live context size
        self.activity = s.get("activity", ["idle", "", None])
        self.prompts = s.get("prompts", [])      # timestamps of the person's prompts, newest last
        self.agent_events = s.get("agent_events", [])   # [ts, tokens used] of subagent calls, for the timeline
        self.opens = s.get("opens", [])          # [ts, source] of each start, resume or /clear, oldest first
        self.base_open = s.get("base_open", [0, 0, 0, 0])   # pruned messages sent after the latest open
        self.ttl = s.get("ttl", 300)             # prompt cache lifetime in seconds: 3600 once a one-hour write is seen
        self.saved = s.get("saved", 0)           # lessons or memory notes written during the session
        self.agent_msgs = {}                     # agent key -> message id -> [ts, 4 values]; live only, never cached

    def state(self, keep=400):
        if len(self.order) > keep:               # fold old messages into the base totals
            for mid in self.order[:-keep]:
                m = self.msgs.pop(mid, None)
                if m:
                    self.base = [a + b for a, b in zip(self.base, m[1:])]
                    if self.opened is not None and (m[0] or 0) >= self.opened:
                        self.base_open = [a + b for a, b in zip(self.base_open, m[1:])]
            self.order = self.order[-keep:]
        if len(self.pending) > 200:
            self.pending = dict(list(self.pending.items())[-200:])
        if len(self.files) > 200:
            self.files = dict(sorted(self.files.items(), key=lambda kv: kv[1][1] or 0)[-200:])
        cut = (self.last_ts or 0) - FLOW_SPAN
        self.agent_events = [e for e in self.agent_events if e[0] >= cut][-2000:]
        self.prompts = self.prompts[-40:]
        return {k: getattr(self, k) for k in self.FIELDS}

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

    def _opened(self, ts, source):
        """A SessionStart hook ran. Startup, resume and clear open the session; a compaction continues it. Several
        hooks run on one start, so records within a minute of the last open are the same open."""
        if source not in OPEN_SOURCES:
            return
        if self.opens and abs(ts - self.opens[-1][0]) < 60:
            return
        self.opens.append([ts, source])
        self.opens = self.opens[-20:]
        self.base_open = [0, 0, 0, 0]            # every message pruned so far was sent before this open

    def _act(self, kind, label, ts):
        if kind != self.activity[0] or label != self.activity[1]:
            self.activity = [kind, label, ts]

    def _claude(self, r):
        t = r.get("type")
        ts = _ts(r.get("timestamp"))
        if t in ("ai-title", "custom-title"):
            self.title = str(r.get("customTitle") or r.get("aiTitle") or self.title)[:60]
            return
        if r.get("isSidechain"):
            return                                  # a subagent's own turns are read from its own transcript
        if r.get("entrypoint") and not self.entry:
            self.entry = str(r["entrypoint"])[:20]
        msg = r.get("message") if isinstance(r.get("message"), dict) else {}
        if t == "assistant":
            u = msg.get("usage")
            if isinstance(u, dict):
                mid = msg.get("id") or r.get("requestId") or r.get("uuid")
                inp, create, read = (u.get("input_tokens") or 0, u.get("cache_creation_input_tokens") or 0,
                                     u.get("cache_read_input_tokens") or 0)
                self._usage(mid, ts, inp, create, read, u.get("output_tokens") or 0)
                self.context = inp + create + read
                if create and self.ttl < 3600:
                    cc = u.get("cache_creation")
                    if isinstance(cc, dict) and (cc.get("ephemeral_1h_input_tokens") or 0) > 0:
                        self.ttl = 3600
            if msg.get("model") and not str(msg["model"]).startswith("<"):
                self.model = str(msg["model"])[:40]
            kinds = [b.get("type") for b in msg.get("content") or [] if isinstance(b, dict)]
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    self._tool_use(b, ts)
            if "tool_use" not in kinds:
                self._act("writing" if "text" in kinds else "thinking", "", ts)
        elif t == "user":
            content = msg.get("content")
            if isinstance(content, str) and "<task-notification>" in content:
                self._notification(content, ts)
            if human_prompt(r, msg):
                self.prompts.append(ts)
                self.recalls.append({"ts": ts, "items": []})
                self.recalls = self.recalls[-12:]
                self._act("thinking", "", ts)
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        self._tool_result(b, r.get("toolUseResult"), ts)
        elif t == "attachment":
            a = r.get("attachment") if isinstance(r.get("attachment"), dict) else {}
            if a.get("hookEvent") == "SessionStart" and ts:
                self._opened(ts, str(a.get("hookName") or "").partition(":")[2])
            if a.get("type") == "hook_additional_context":
                parts = a.get("content")
                for p in parts if isinstance(parts, list) else [parts]:
                    if isinstance(p, str):
                        self._recalled(p, ts)
            elif a.get("type") == "queued_command":
                raw = a.get("prompt") if isinstance(a.get("prompt"), str) else json.dumps(a)
                if "<task-notification>" in raw:
                    self._notification(raw.replace("\\n", "\n"), ts)
            elif a.get("type") == "task_status":
                desc, status = a.get("description"), a.get("status")
                for ag in self.agents.values():
                    if ag.get("desc") == desc and ag.get("status") in ("running", "background"):
                        ag["status"] = "running" if status == "running" else (status or ag["status"])
        elif t == "system" and r.get("subtype") == "turn_duration":
            self._act("idle", "", ts)

    def _tool_use(self, b, ts):
        name, ti, tid = b.get("name") or "", b.get("input") if isinstance(b.get("input"), dict) else {}, b.get("id")
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        self._act("tool", tool_label(name, ti), ts)
        if name in READ_TOOLS and path:
            self.pending[tid] = ["read", path]
            self.files[path] = [self.files.get(path, ["read"])[0], ts]
        elif name in WRITE_TOOLS and path:
            self.pending[tid] = ["write", path]
            self.files[path] = ["written", ts]
            if path.endswith(".md") and SAVE_PATH_RE.search(path):
                self.saved += 1
        elif name == "Bash":
            self.pending[tid] = ["shell", short_command(ti.get("command"))]
            cmd = ti.get("command")
            if isinstance(cmd, str) and " add" in cmd and SAVE_COMMAND_RE.search(cmd):
                self.saved += 1
        elif name in AGENT_TOOLS:
            desc = str(ti.get("description") or ti.get("subagent_type") or "agent")[:60]
            self.agents[tid] = {"name": desc, "desc": ti.get("description"), "type": str(ti.get("subagent_type") or "")[:40],
                                "status": "running", "tokens": 0, "used": 0, "cache": 0, "ms": 0, "ts": ts}
            self.pending[tid] = ["agent", desc]
        else:
            self.pending[tid] = ["tool", name]

    def _tool_result(self, b, tur, ts):
        tid = b.get("tool_use_id")
        kind = self.pending.pop(tid, None)
        est = _text_size(b.get("content")) // 4
        if kind and kind[0] == "read":
            self.reads[kind[1]] = self.reads.get(kind[1], 0) + est
        elif kind and kind[0] == "shell":
            self.shell[kind[1]] = self.shell.get(kind[1], 0) + est
        if not self.pending:
            self._act("thinking", "", ts)
        ag = self.agents.get(tid)
        if ag is not None and isinstance(tur, dict):
            if tur.get("agentId"):
                ag["aid"] = str(tur["agentId"])[:40]
            if tur.get("status") == "async_launched" or tur.get("isAsync"):
                ag["status"] = "background"
            else:
                ag["status"] = str(tur.get("status") or ("error" if b.get("is_error") else "completed"))[:12]
                if not ag.get("live"):
                    ag["tokens"] = ag["used"] = int(tur.get("totalTokens") or 0)
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
            if _tag(block, "task-id"):
                ag.setdefault("aid", _tag(block, "task-id")[:40])
            tok = re.search(r"(?:total_tokens|subagent_tokens)\D{0,4}(\d+)", _tag(block, "usage") or "")
            ms = re.search(r"duration_ms\D{0,4}(\d+)", _tag(block, "usage") or "")
            if tok and not ag.get("live"):
                ag["tokens"] = ag["used"] = int(tok.group(1))
            if ms:
                ag["ms"] = int(ms.group(1))
            if ag["status"] not in ("running", "background"):
                ag["done_ts"] = ts

    def _recall_group(self, ts):
        if not self.recalls:
            self.recalls.append({"ts": ts, "items": []})
        return self.recalls[-1]["items"]

    def _recalled(self, text, ts):
        """Memory and lesson titles from a hook's added context, in every format the harness and ai-os write."""
        items = self._recall_group(ts)
        mode, where = None, ""
        for line in text.split("\n"):
            s = line.strip()
            if MEMORY_MARK in s:
                mode = "memory"
                continue
            if s.startswith(LESSON_MARK):
                mode, where = "lesson", ""
                continue
            m = FILE_LESSON_RE.match(s)
            if m:
                mode, where = "lesson", os.path.basename(m.group(1))
                continue
            if REPO_CARD_RE.match(s):
                mode = "card"
                continue
            if mode is None:
                continue
            m = TITLE_RE.match(line)
            if m and mode in ("memory", "card"):
                title = memory_title(m.group(1))
                if mode == "memory":
                    if title in self.memories:
                        self.memories.remove(title)
                    self.memories.append(title)
                items.append(["memory" if mode == "memory" else "repo card", title, ""])
                continue
            m = LESSON_LINE_RE.match(line)
            if m and mode == "lesson":
                why = MATCHED_RE.search(s)
                items.append(["lesson", memory_title(m.group(1)), ("for " + where) if where else
                              ("matched " + why.group(1)) if why else ""])
                continue
            if s and not line.startswith((" ", "\t")) and not s.startswith(("-", "*")):
                mode = None
        del items[:-12]
        self.memories = self.memories[-30:]

    def _codex(self, r):
        self.host = "codex"
        p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        if r.get("type") == "event_msg" and p.get("type") == "token_count":
            u = ((p.get("info") or {}).get("last_token_usage")) or {}
            cached = u.get("cached_input_tokens") or 0
            mid = f"codex-{self.turns}"
            inp = max(0, (u.get("input_tokens") or 0) - cached)
            self._usage(mid, _ts(r.get("timestamp")), inp, u.get("cache_write_input_tokens") or 0, cached,
                        u.get("output_tokens") or 0)
            self.context = inp + cached

    def feed_agent(self, aid, meta, rows):
        """Rows from one subagent's own transcript: its tokens and current tool, live."""
        tid = meta.get("toolUseId")
        key = tid if tid in self.agents else None
        if key is None:
            key = next((k for k, a in self.agents.items() if a.get("aid") == aid), "agent:" + aid)
        ag = self.agents.get(key)
        if ag is None:
            desc = str(meta.get("description") or meta.get("agentType") or "subagent")[:60]
            ag = self.agents[key] = {"name": desc, "desc": meta.get("description"), "type": str(meta.get("agentType") or "")[:40],
                                     "status": "running", "tokens": 0, "used": 0, "cache": 0, "ms": 0, "ts": None,
                                     "nested": True}
        ag["aid"] = aid
        per = self.agent_msgs.setdefault(key, {})
        for r in rows:
            ts = _ts(r.get("timestamp"))
            if ts and not ag.get("ts"):
                ag["ts"] = ts
            got = usage.usage_of(r) if r.get("type") == "assistant" else None
            if got:
                if got[0] not in per:
                    self.agent_events.append([ts or 0, usage.used(got[2])])
                per[got[0]] = got[2]
                ag["seen"] = ts
            msg = r.get("message") if isinstance(r.get("message"), dict) else {}
            if r.get("type") == "assistant":
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        ag["last"] = tool_label(b.get("name") or "", b.get("input"))
                ag["ended"] = ts if msg.get("stop_reason") == "end_turn" else None
        if ag.get("ended") and ag.get("status") in ("running", "background"):
            ag["status"], ag["done_ts"] = "completed", ag["ended"]      # its own transcript says it has answered
            ag["ms"] = int(max(0, ag["ended"] - (ag.get("ts") or ag["ended"])) * 1000)
        t = [0, 0, 0, 0]
        for v in per.values():
            t = [a + b for a, b in zip(t, v)]
        if per:
            ag["live"] = True
            ag["used"] = ag["tokens"] = usage.used(t)
            ag["cache"] = t[2]

    def resolve_agents(self, sub_dir):
        """Finished subagents whose own transcript has not been read: read it once for exact figures."""
        for ag in self.agents.values():
            if ag.get("live") or not ag.get("aid") or ag.get("status") in ("running", "background"):
                continue
            t = agent_usage(os.path.join(sub_dir, "agent-%s.jsonl" % ag["aid"]))
            if t and any(t):
                ag["live"] = True
                ag["used"] = ag["tokens"] = usage.used(t)
                ag["cache"] = t[2]

    def feed_signals(self, rows):
        for r in rows:
            if r.get("kind") == "gate" and r.get("rule"):
                self.gates.append(str(r["rule"])[:40])
                self.gates = self.gates[-20:]
            elif r.get("kind") == "learner" and (self.first_ts is None or (r.get("ts") or 0) >= self.first_ts):
                self.learner += int(r.get("applied") or 0) if not isinstance(r.get("applied"), list) else len(r["applied"])

    # -- derived numbers

    @property
    def opened(self):
        return self.opens[-1][0] if self.opens else None

    def scope(self):
        """The time this session counts from, or None when that is the start of the whole conversation."""
        o = self.opened
        return o if o is not None and self.first_ts is not None and o > self.first_ts + 60 else None

    def session_agents(self, since=None):
        since = self.scope() if since is None else since
        return [a for a in self.agents.values() if since is None or (a.get("ts") or 0) >= since or agent_live(a)]

    def totals(self, since=None):
        """Token figures for the whole conversation, or for the messages and subagents since `since`."""
        inp, create, read, out = self.base if since is None else self.base_open
        for m in self.msgs.values():
            if since is None or (m[0] or 0) >= since:
                inp, create, read, out = inp + m[1], create + m[2], read + m[3], out + m[4]
        ags = self.agents.values() if since is None else [a for a in self.agents.values()
                                                          if (a.get("ts") or 0) >= since]
        agents = sum(a.get("used", a.get("tokens")) or 0 for a in ags)
        agent_cache = sum(a.get("cache") or 0 for a in ags)
        return {"cache_read": read + agent_cache, "new_input": inp + create, "input": inp, "cache_write": create,
                "output": out, "subagents": agents, "used": inp + create + out + agents,
                "total": read + inp + create + out + agents}

    def request(self):
        """Tokens used since the person's last prompt, the number of model calls and when it started."""
        start = self.prompts[-1] if self.prompts else None
        if start is None:
            return {"used": 0, "calls": 0, "start": None}
        used = calls = 0
        for m in self.msgs.values():
            if m[0] and m[0] >= start:
                used += m[1] + m[2] + m[4]
                calls += 1
        used += sum(a.get("used", a.get("tokens")) or 0 for a in self.agents.values() if (a.get("ts") or 0) >= start)
        return {"used": used, "calls": calls, "start": start}

    def flow(self, now, cols, span=FLOW_SPAN):
        """Tokens used per time slice over the last `span` seconds, oldest first, subagents included."""
        width = span / float(cols)
        out = [0] * cols
        start = now - span

        def put(ts, n):
            if ts and start <= ts <= now + 5:
                out[min(cols - 1, int((ts - start) / width))] += n
        for m in self.msgs.values():
            put(m[0], m[1] + m[2] + m[4])
        for ts, n in self.agent_events:
            put(ts, n)
        return out

    def burn_per_min(self, now=None, window=BURN_WINDOW):
        now = time.time() if now is None else now
        n = 0
        for m in self.msgs.values():
            if m[0] and now - window <= m[0] <= now + 5:
                n += m[1] + m[2] + m[4]
        for ts, used in self.agent_events:
            if now - window <= ts <= now + 5:
                n += used
        for a in self.agents.values():
            t = a.get("done_ts")
            if t and not a.get("live") and now - window <= t <= now + 5:
                n += a.get("used", a.get("tokens")) or 0
        return n / (window / 60.0)

    def running_agents(self, now=None):
        return sum(1 for a in self.agents.values() if agent_live(a, now))

    def working(self, now):
        """True while the model or a subagent is doing something; an unfinished turn older than 15 minutes is idle."""
        kind, _, since = self.activity
        busy = kind != "idle" and (since is None or now - since < 900)
        return busy or self.running_agents(now) > 0

    def window(self):
        return 1_000_000 if "[1m]" in self.model or self.context > 200_000 else 200_000

    def where(self, cwd=None, limit=6):
        rows = [("Replies written", self.totals(self.scope())["output"])]
        rows += [("Read " + short_path(p, cwd, 40), n) for p, n in self.reads.items()]
        rows += [("Agent " + a["name"], a.get("used", a.get("tokens")) or 0) for a in self.session_agents()]
        rows += [("Shell " + c, n) for c, n in self.shell.items()]
        rows = [r for r in rows if r[1] > 0]
        return sorted(rows, key=lambda r: -r[1])[:limit]

    def recent_files(self, limit=8):
        items = sorted(self.files.items(), key=lambda kv: kv[1][1] or 0)
        return [(p, v[0]) for p, v in items[-limit:]][::-1]


STALL = 1800


def agent_live(a, now=None):
    """Running, and heard from in the last half hour (a subagent silent that long is shown as stalled, not running)."""
    if a.get("status") not in ("running", "background"):
        return False
    last = a.get("seen") or a.get("ts")
    return now is None or last is None or now - last < STALL


_AUTO = {}


def autocompact_pct():
    """The context percentage at which Claude Code compacts on its own, when the person set one
    (CLAUDE_AUTOCOMPACT_PCT_OVERRIDE, in the environment or in Claude Code's settings.json "env"); else None.
    The settings file is read at most every 30 seconds, since the panel asks on every frame."""
    raw = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE")
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    key = (raw, base)
    hit = _AUTO.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    pct = _autocompact_pct(raw, base)
    _AUTO.clear()
    _AUTO[key] = (time.time(), pct)
    return pct


def _autocompact_pct(raw, base):
    if raw is None:
        try:
            with open(os.path.join(base, "settings.json")) as fh:
                env = json.load(fh).get("env") or {}
            raw = env.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE") if isinstance(env, dict) else None
        except (OSError, ValueError, AttributeError):
            raw = None
    try:
        pct = int(float(raw))
    except (TypeError, ValueError):
        return None
    return pct if 5 <= pct <= 100 else None


def suggestions(stats, now=None, budget=None, limit=3):
    """What the person could do next in Claude Code, from this session's own context, cache, memory and subagents,
    most urgent first, as (rank, sentence). Empty when nothing needs changing."""
    now = time.time() if now is None else now
    out = []
    ctx, win = stats.context, stats.window()
    f = ctx / float(win) if ctx else 0.0
    auto = autocompact_pct()
    warn = (auto - 10) / 100.0 if auto else 0.6
    idle = not stats.working(now)
    if ctx and f >= warn:
        if auto:
            text = (f"Context is {int(f * 100)}% full ({fmt_tokens(ctx)} of {fmt_tokens(win)}), and Claude Code "
                    f"compacts on its own at {auto}%. Type /compact now, followed by what to keep, so you choose what "
                    f"the summary holds.")
        else:
            text = (f"Context is {int(f * 100)}% full ({fmt_tokens(ctx)} of {fmt_tokens(win)}), and every call "
                    f"re-reads all of it. Type /compact, followed by what to keep, to continue with a smaller context.")
        out.append((1, text))
    last = stats.last_ts
    if idle and last and ctx >= 20_000:
        gone = now - last
        life = "one hour" if stats.ttl >= 3600 else "five minutes"
        if gone >= stats.ttl:
            out.append((2, f"The prompt cache expired after {life} idle, so your next message writes about "
                           f"{fmt_tokens(ctx)} tokens back into it. If you are starting a different task, type /clear "
                           f"first."))
        elif stats.ttl - gone <= CACHE_WARN and stats.ttl >= 3600:
            left = max(1, int((stats.ttl - gone) // 60))
            out.append((4, f"The prompt cache expires in {left} min. Reply before then to keep the "
                           f"{fmt_tokens(ctx)} of context cached, or type /clear if this task is finished."))
    if idle and ctx and f >= 0.5:
        if stats.saved:
            n = stats.saved
            out.append((3, f"{n} lesson{'s were' if n != 1 else ' was'} saved this session, so a new session recalls "
                           f"{'them' if n != 1 else 'it'}. If this task is finished, type /clear to start again with a "
                           f"small context."))
        else:
            out.append((3, "Nothing from this session has been saved to memory yet. Ask Claude to record what it "
                           "learned before you type /clear, or it will not be recalled later."))
    for a in stats.session_agents():
        if a.get("status") in ("running", "background") and not agent_live(a, now):
            out.append((3, f"Subagent \"{a['name']}\" has been silent for 30 minutes. Ask Claude to check on it or "
                           f"stop it."))
            break
    b = budget or budgets()
    spent = billable(stats.totals(stats.scope())) / max(1, b["session"])
    if spent >= 0.8:
        out.append((2, f"This session has used {int(spent * 100)}% of its token budget. Type /compact to shrink the "
                       f"context, or /clear if the task is finished."))
    out.sort(key=lambda x: x[0])
    return out[:limit]


SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def dashboard_offer(ev, cli, env=None):
    """Context for SessionStart: ask the person once whether to open the token dashboard for this session, and how to
    open it where they are (the desktop app's Terminal panel, a tmux split, or a second terminal). Empty when the
    panel is already open (started by `nodaris`), after a compaction, for other hosts, or when switched off."""
    env = os.environ if env is None else env
    sid = str(ev.get("session_id") or "")
    if ev.get("host") != "claude" or ev.get("source") not in (None, "startup", "resume") or env.get("NODARIS_PANEL_LINK"):
        return ""
    if load_settings().get("dashboard_offer") is False or not SESSION_ID_RE.match(sid):
        return ""
    link = session_link(env)
    cmd = " ".join(shlex.quote(x) for x in (cli, "watch", "--host", "claude", "--session", sid)
                   + (("--link", link) if link else ()))
    ask = ("Token dashboard (from the Nodaris harness): in your first reply of this session, ask the person once, "
           "with the AskUserQuestion tool, whether to open the live token dashboard for this session, with the options "
           "\"Open the dashboard\" and \"Not now\". Then carry on with their request. Do not ask again in this session.")
    if env.get("CLAUDE_CODE_ENTRYPOINT") == "claude-desktop":
        how = ("If they choose to open it, run this command in the app's Terminal panel with the terminal tool "
               "(mcp__terminal__run_in_terminal; load it with ToolSearch first if it is deferred): " + cmd)
    elif env.get("TMUX"):
        how = ("If they choose to open it, open it beside this session with the Bash tool: tmux split-window -h -l 44% "
               + shlex.quote(cmd))
    else:
        how = ("If they choose to open it, give them this command to run in a second terminal window: " + cmd
               + " . Tell them that starting Claude Code with `nodaris` opens the dashboard beside the session on its "
               "own, when tmux is installed.")
    off = f" They can stop this question by setting \"dashboard_offer\": false in {os.path.join(policy.home(), 'settings.json')}."
    return ask + " " + how + off


def signals_path(session_id):
    return os.path.join(policy.home(), "signals", hashlib.sha256(str(session_id).encode()).hexdigest()[:24] + ".jsonl")


def gate_spent(session_id):
    """What the subagent budget gate has charged this session so far, from its own state file."""
    try:
        from . import capsule
        return int(capsule.load(session_id).get("spent") or 0)
    except Exception:  # noqa: BLE001
        return 0


# ---- the live source -------------------------------------------------------------------------------------------------

class Source:
    """A transcript, its subagents' transcripts, its signals file and the learner's, each tailed incrementally."""

    def __init__(self, path, host="claude"):
        self.path = path
        self.stats = SessionStats({"host": host})
        self.tail = Tail(path) if path else None
        self.sid = os.path.splitext(os.path.basename(path or ""))[0]
        self.sub_dir = os.path.join(os.path.dirname(path or ""), self.sid, "subagents") if path else ""
        self.sig = [Tail(signals_path(self.sid)), Tail(signals_path("learner"))]
        self.subs = {}                           # agent id -> (Tail, meta)
        self._scan = 0.0

    def _scan_agents(self):
        for p in glob.glob(os.path.join(self.sub_dir, "agent-*.jsonl")):
            aid = os.path.basename(p)[len("agent-"):-len(".jsonl")]
            if aid in self.subs:
                continue
            try:
                with open(p[:-len(".jsonl")] + ".meta.json") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                meta = {}
            self.subs[aid] = (Tail(p), meta if isinstance(meta, dict) else {})

    def poll(self, now=None):
        if self.tail:
            self.stats.feed(self.tail.read_new())
        now = time.time() if now is None else now
        if self.sub_dir and now - self._scan >= 2.0:
            self._scan = now
            self._scan_agents()
        for aid, (tail, meta) in self.subs.items():
            rows = tail.read_new()
            if rows:
                self.stats.feed_agent(aid, meta, rows)
        for t in self.sig:
            self.stats.feed_signals(t.read_new())
        return self.stats

    def caught_up(self):
        return not self.tail or self.tail.offset >= self.tail.size() - 1


# ---- drawing -----------------------------------------------------------------------------------------------------

def _mix(a, b, f):
    f = max(0.0, min(1.0, f))
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def heat(burn):
    """0 when idle, 1 at about 200k tokens a minute, on a log scale."""
    if burn <= 0:
        return 0.0
    return max(0.0, min(1.0, math.log10(1 + burn) / math.log10(200_000)))


class Motion:
    """Numbers that count up to their new value instead of jumping, and a short-lived "+N" after each increase."""

    def __init__(self):
        self.shown, self.target, self.delta = {}, {}, {}

    def value(self, key, target, now):
        prev = self.target.get(key)
        if prev is not None and target > prev:
            old = self.delta.get(key)
            carried = old[0] if old and now - old[1] < 4 else 0
            self.delta[key] = [carried + target - prev, now]
        self.target[key] = target
        cur = self.shown.get(key, target)
        cur = cur + (target - cur) * 0.35
        if abs(target - cur) <= max(1, target * 0.002):
            cur = target
        self.shown[key] = cur
        return cur

    def recent(self, key, now, span=4.0):
        """(amount, fade 1..0) of the last increase, while it is fresh."""
        d = self.delta.get(key)
        if not d or now - d[1] > span:
            return None
        return d[0], 1.0 - (now - d[1]) / span


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


def context_rgb(fraction):
    return RED if fraction >= 0.92 else AMBER if fraction >= 0.8 else TEAL


def spinner(t, unicode=True):
    return SPIN[int(t * 10) % len(SPIN)] if unicode else "|/-\\"[int(t * 8) % 4]


def activity_line(stats, cv, now, t, unicode=True):
    kind, label, since = stats.activity
    run = stats.running_agents(now)
    if not stats.working(now):
        text = "Waiting for you"
        return cv.seg("● " if unicode else "* ", tui.MUTED) + cv.seg(text, tui.MUTED)
    if kind == "idle":
        text, since = f"{run} subagent{'s' if run != 1 else ''} still working", None
    else:
        text = {"thinking": "Thinking", "writing": "Writing the reply", "tool": label or "Using a tool"}.get(kind, "Working")
    pulse = 0.5 + 0.5 * math.sin(t * 5)
    el = f"  {fmt_ms((now - since) * 1000)}" if since else ""
    return cv.seg(spinner(t, unicode) + " ", _mix(TEAL, TEAL_LIGHT, pulse)) + text + cv.seg(el, tui.MUTED)


def flow_lines(stats, cv, cols, now, t, unicode=True, height=3):
    """A timeline of tokens used over the last ten minutes, newest on the right; prompts marked underneath."""
    n = max(10, cols)
    vals = stats.flow(now, n)
    peak = max(vals) or 1
    levels = height * 8
    glyphs = BLOCKS if unicode else " .:-=+*#%"
    working = stats.working(now)
    rows = []
    for row in range(height):                        # row 0 is the top
        segs = []
        for i, v in enumerate(vals):
            h = 0 if v <= 0 else max(1, int(round(math.sqrt(v / peak) * levels)))
            fill = h - (height - 1 - row) * 8
            if fill <= 0:
                segs.append(" ")
                continue
            ch = glyphs[min(8, fill)]
            shade = 0.25 + 0.75 * math.sqrt(v / peak)
            if i == n - 1 and working:
                shade = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(t * 6))
            segs.append(cv.seg(ch, _mix(TEAL_DEEP, TEAL_LIGHT, shade)))
        rows.append("".join(segs))
    marks = [" "] * n
    for p in stats.prompts:
        if p and now - FLOW_SPAN <= p <= now:
            marks[min(n - 1, int((p - (now - FLOW_SPAN)) / (FLOW_SPAN / float(n))))] = "▲" if unicode else "^"
    axis = "-10m".ljust(n // 2) + "-5m"
    axis = axis.ljust(n - 3) + "now"
    rows.append(cv.seg("".join(marks), TEAL))
    rows.append(cv.seg(axis[:n], tui.MUTED))
    return rows, (fmt_tokens(max(vals)) if any(vals) else "")


def when_text(ts, now):
    """A time on the same day, or a short date for an earlier one."""
    d, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    return d.strftime("%H:%M") if d.date() == n.date() else d.strftime("%b ") + str(d.day) + d.strftime(" %H:%M")


def wrap_text(text, width, first, rest):
    """Word-wrap one sentence into lines that start with `first` and then `rest`."""
    width = max(20, width)
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = (cur + " " + word) if cur else word
    if cur:
        lines.append(cur)
    return [(first if i == 0 else rest) + l for i, l in enumerate(lines)]


def _head(cv, title, w, note="", unicode=True):
    rule = "─" if unicode else "-"
    note = (" " + note + " ") if note else ""
    fill = max(0, w - len(title) - 1 - len(note))
    return cv.seg(title + " ", tui.ACCENT, bold=True) + cv.seg(rule * fill, tui.MUTED) + cv.seg(note, tui.MUTED)


def blocks(stats, cv, width, now, t, cwd=None, unicode=True, budget=None, motion=None, ledger=None, session=None):
    """The panel as ordered (title, note, lines) blocks; the frame lays them out in one or two columns."""
    motion = motion or Motion()
    b = budget or budgets()
    since = stats.scope()
    tot = stats.totals(since)
    used = motion.value("used", tot["used"], now)
    req = stats.request()
    req_used = motion.value("req", req["used"], now)
    lab = 14
    num = lambda n: fmt_tokens(n).rjust(7)                              # noqa: E731
    lines = []
    d = motion.recent("used", now)
    badge = cv.seg(f"  +{fmt_tokens(d[0])}", _mix(tui.MUTED, TEAL_LIGHT, d[1])) if d else ""
    start = stats.opened or stats.first_ts
    lines.append("This session".ljust(lab) + cv.seg(num(used), tui.ACCENT, bold=True) + " used"
                 + (cv.seg(" since " + when_text(start, now), tui.MUTED) if start else "") + badge)
    parts = [f"{fmt_tokens(tot['cache_write'])} written to cache", f"{fmt_tokens(tot['output'])} output"]
    if tot["input"]:
        parts.append(f"{fmt_tokens(tot['input'])} new input")
    if tot["subagents"]:
        parts.append(f"{fmt_tokens(tot['subagents'])} subagents")
    lines.append(" " * lab + cv.seg(" · ".join(parts), tui.MUTED))
    lines.append(" " * lab + cv.seg(f"{fmt_tokens(tot['cache_read'])} cache re-reads, about 1/10 the price", tui.MUTED))
    if since is not None and stats.first_ts:
        whole = stats.totals()
        lines.append("Whole conversation" + cv.seg(f" {fmt_tokens(whole['used'])} used since "
                                                    + when_text(stats.first_ts, now), tui.MUTED))
    if req["start"]:
        dur = fmt_ms((now - req["start"]) * 1000) if stats.working(now) else ""
        lines.append("This request".ljust(lab) + num(req_used) + cv.seg(
            f" used · {req['calls']} call{'s' if req['calls'] != 1 else ''}" + (f" · {dur}" if dur else ""), tui.MUTED))
    bw = max(6, min(24, width - lab - 22))
    if stats.context:
        win = stats.window()
        f = stats.context / float(win)
        ctx = motion.value("ctx", stats.context, now)
        lines.append("Context".ljust(lab) + num(ctx) + " " + cv.seg(bar(f, bw, unicode), context_rgb(f))
                     + cv.seg(f" {int(f * 100)}% of {fmt_tokens(win)}", tui.MUTED))
    f = billable(tot) / max(1, b["session"])
    lines.append("Budget".ljust(lab) + num(billable(tot)) + " " + cv.seg(bar(f, bw, unicode), level_rgb(f))
                 + cv.seg(f" {int(f * 100)}% of {fmt_tokens(b['session'])}", tui.MUTED))
    out = [("", "", lines)]

    tips = suggestions(stats, now, b)
    s_lines = []
    for _, text in tips:
        s_lines += wrap_text(text, width - 2, cv.seg("› " if unicode else "> ", TEAL), "  ")
    out.append(("Suggestions", f"{len(tips)} now" if tips else "",
                s_lines or [cv.seg("Nothing to change. Context and cache are healthy.", tui.MUTED)]))

    flow, peak = flow_lines(stats, cv, width, now, t, unicode)
    out.append(("Token flow", (f"peak {peak} per 10s" if width >= 60 else f"peak {peak}") if peak else "quiet", flow))

    a_lines = []
    agents = sorted(stats.session_agents(since), key=lambda a: (not agent_live(a, now), -(a.get("done_ts") or a.get("ts") or 0)))
    for a in agents[:6]:
        st = a.get("status") or ""
        live = agent_live(a, now)
        if st in ("running", "background") and not live:
            a_lines.append(cv.seg("…" if unicode else ".", tui.MUTED) + " " + pad(a["name"], max(8, width - 24)) + " "
                           + fmt_tokens(a.get("used", a.get("tokens")) or 0).rjust(6) + " " + cv.seg("stalled", tui.MUTED))
            continue
        if live:
            glyph, rgb = spinner(t + (a.get("ts") or 0) % 1, unicode), TEAL_LIGHT
            ms = (now - a["ts"]) * 1000 if a.get("ts") else 0
        else:
            ok = st == "completed"
            glyph, rgb = ("✓" if unicode else "+") if ok else ("✗" if unicode else "x"), TEAL if ok else RED
            ms = a.get("ms") or 0
        tok = motion.value("agent:" + (a.get("aid") or a["name"]), a.get("used", a.get("tokens")) or 0, now)
        name_w = max(8, width - 24)
        a_lines.append(cv.seg(glyph, rgb) + " " + pad(a["name"], name_w) + " " + fmt_tokens(tok).rjust(6) + " "
                       + cv.seg(fmt_ms(ms).rjust(6), tui.MUTED))
        if live and a.get("last"):
            a_lines.append(cv.seg(("  └ " if unicode else "  - ") + a["last"], tui.MUTED))
    if agents:
        spent = gate_spent(session) if session else 0
        spent += sum(a.get("used", 0) + a.get("cache", 0) // 10 for a in stats.agents.values() if agent_live(a, now))
        f = spent / max(1, b["agents"])
        a_lines.append(cv.seg("Budget ", tui.MUTED) + cv.seg(bar(f, bw, unicode), level_rgb(f))
                       + cv.seg(f" {int(f * 100)}% of {fmt_tokens(b['agents'])} for subagents", tui.MUTED))
    run = stats.running_agents(now)
    out.append(("Subagents", f"{run} running" if run else "", a_lines or [cv.seg("none this session", tui.MUTED)]))

    m_lines = []
    group = next((g for g in reversed(stats.recalls) if g["items"]), None)
    if group:
        when = datetime.fromtimestamp(group["ts"]).strftime("%H:%M") if group.get("ts") else ""
        latest = group is stats.recalls[-1]
        m_lines.append(cv.seg(("For your last request" if latest else "For an earlier request") + (f", {when}" if when else ""),
                              tui.MUTED))
        dot = "• " if unicode else "- "
        for kind, title, why in group["items"][-6:]:
            tag = {"lesson": "lesson ", "repo card": "repo card "}.get(kind, "")
            m_lines.append(cv.seg(dot, TEAL) + cv.seg(tag, tui.MUTED) + title + (cv.seg("  " + why, tui.MUTED) if why else ""))
    out.append(("Memories pulled", "", m_lines or [cv.seg("none recalled yet", tui.MUTED)]))

    where = stats.where(cwd)
    top = max([n for _, n in where] or [1])
    ww = max(4, min(12, width // 5))
    w_lines = [pad(label, max(8, width - ww - 9)) + " " + cv.seg(bar(n / top, ww, unicode), TEAL) + " " + fmt_tokens(n).rjust(6)
               for label, n in where]
    out.append(("Where the tokens went", "", w_lines or [cv.seg("nothing measured yet", tui.MUTED)]))
    f_lines = [cv.seg(("W " if mode == "written" else "R "), TEAL if mode == "written" else tui.MUTED) + short_path(p, cwd, width - 3)
               for p, mode in stats.recent_files(6)]
    out.append(("Files", "", f_lines or [cv.seg("none touched yet", tui.MUTED)]))

    if ledger is None:
        o_lines = [cv.seg("Counting your sessions...", tui.MUTED)]
    else:
        td, wk = ledger["today"], ledger["week"]
        o_lines = ["Today".ljust(lab) + num(td["used"]) + cv.seg(
                       f" used · {td['sessions']} session{'s' if td['sessions'] != 1 else ''}", tui.MUTED),
                   "Last 7 days".ljust(lab) + num(wk["used"]) + cv.seg(
                       f" used · {wk['sessions']} sessions" + ("" if ledger.get("complete") else " · still counting"),
                       tui.MUTED)]
    out.append(("All your sessions", "", o_lines))
    return out


def sections(stats, cv, width, cwd=None, unicode=True, now=None):
    """The titled panels as {title: [lines]}, for the plain summary."""
    now = time.time() if now is None else now
    return {title: lines for title, _, lines in blocks(stats, cv, width, now, 0.0, cwd, unicode)
            if title not in ("", "Token flow", "All your sessions")}


def header(stats, cv, cols, now, t, path, unicode=True):
    live = stats.working(now)
    dot = cv.seg("● live" if live else "○ idle", _mix(TEAL, TEAL_LIGHT, 0.5 + 0.5 * math.sin(t * 3)) if live else tui.MUTED)
    title = cv.seg("NODARIS", tui.ACCENT, bold=True) + "  token monitor"
    top = title + " " * max(1, cols - tui.visible_len(title) - tui.visible_len(dot)) + dot
    where = {"cli": "CLI", "claude-desktop": "desktop app", "claude-vscode": "VS Code"}.get(stats.entry, stats.entry or "")
    sid = os.path.splitext(os.path.basename(path or ""))[0][:8]
    name = f"\"{stats.title}\"" if stats.title else "untitled session"
    sub = cv.seg("Watching " + " · ".join(x for x in (where, sid, name) if x), tui.MUTED)
    return [top, sub, activity_line(stats, cv, now, t, unicode)]


def render_frame(stats, cols, rows, t=0.0, mode="256", cwd=None, unicode=True, now=None, budget=None, path=None,
                 motion=None, ledger=None):
    """One full frame of the live panel as a list of lines."""
    now = time.time() if now is None else now
    cv = Canvas(mode)
    session = os.path.splitext(os.path.basename(path or ""))[0] or None
    lines = header(stats, cv, cols, now, t, path, unicode) + [""]
    two = cols >= 110
    width = (cols - 3) // 2 if two else cols - 1
    bl = blocks(stats, cv, width, now, t, cwd, unicode, budget, motion, ledger, session)
    if two:
        left, right = [], []
        for i, (title, note, body) in enumerate(bl):
            side = left if i < 3 else right
            side += ([_head(cv, title, width, note, unicode)] if title else []) + body + [""]
        for i in range(max(len(left), len(right))):
            lines.append(pad(left[i] if i < len(left) else "", width) + "   " + (right[i] if i < len(right) else ""))
    else:
        for title, note, body in bl:
            lines += ([_head(cv, title, width, note, unicode)] if title else []) + body + [""]
    return [fit(l, cols) for l in lines[:rows]]


def render_plain(stats, cwd=None, now=None, budget=None, path=None, ledger=None):
    """One summary block with no escape codes, for pipes, NO_COLOR and reduced motion."""
    now = time.time() if now is None else now
    b = budget or budgets()
    since = stats.scope()
    tot = stats.totals(since)
    burn = stats.burn_per_min(now)
    used = billable(tot) / max(1, b["session"])
    req = stats.request()
    start = stats.opened or stats.first_ts
    out = ["Nodaris token monitor" + (f" ({short_path(path, None, 60)})" if path else ""),
           f"Tokens used this session{' since ' + when_text(start, now) if start else ''}: {tot['used']:,} "
           f"(cache writes {tot['cache_write']:,}, output {tot['output']:,}, new input {tot['input']:,}, "
           f"subagents {tot['subagents']:,})",
           f"Cache re-reads: {tot['cache_read']:,}, billed at about a tenth of the input price, not counted above",
           f"This request: {req['used']:,} tokens used in {req['calls']} call(s)",
           f"Burn rate: {int(burn):,} tokens used per minute over the last 5 minutes",
           f"Session budget: {int(used * 100)}% of {b['session']:,} ({b['plan']} plan, {level(used)})",
           f"Agent budget: {int(tot['subagents'] / max(1, b['agents']) * 100)}% of {b['agents']:,}",
           f"Gate stops: {len(stats.gates)}" + (f"; learner changes: {stats.learner}" if stats.learner else "")]
    if since is not None and stats.first_ts:
        out.insert(2, f"Whole conversation since {when_text(stats.first_ts, now)}: {stats.totals()['used']:,} tokens used")
    if stats.context:
        out.insert(4, f"Context now: {stats.context:,} of {stats.window():,} tokens")
    for title, body in sections(stats, Canvas("none"), 72, cwd, unicode=False, now=now).items():
        out += ["", title + ":"] + ["  " + l.rstrip() for l in body]
    if ledger:
        out += ["", f"All your sessions: {ledger['today']['used']:,} tokens used today across "
                    f"{ledger['today']['sessions']} session(s); {ledger['week']['used']:,} in the last 7 days"]
    return "\n".join(out)


# ---- commands --------------------------------------------------------------------------------------------------------

def cli_command():
    exe = shutil.which("nodaris-harness")
    return shlex.quote(exe) if exe else f"{shlex.quote(sys.executable)} -m nodaris_harness"


def split(a, out=None):
    """Open the panel beside the agent: a tmux pane, a new macOS terminal tab, or one sentence on how."""
    out = out or sys.stdout
    cmd = cli_command() + " watch" + (f" --session {shlex.quote(a.session)}" if getattr(a, "session", None) else "") \
        + (f" --link {shlex.quote(a.link)}" if getattr(a, "link", None) else "")
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
            # Owner-only (0700) launcher script that Terminal has to be able to execute; no group or other access.
            os.chmod(script, 0o700)  # nosemgrep
            subprocess.run(["open", "-a", "iTerm" if prog == "iTerm.app" else "Terminal", script], check=False)
            out.write("The token monitor is opening in a new terminal window.\n")
            return 0
        out.write(f"Open a new tab in this directory and run: {cmd}\n(Add --open to have it opened for you.)\n")
        return 0
    out.write(f"Side panes need tmux, so run this in a second terminal beside your agent: {cmd}\n")
    return 0


class LedgerFeed:
    """Keeps the cross-session figures fresh in a background thread, so the panel never waits on them."""

    def __init__(self, every=20.0):
        self.summary, self.every, self._stop = None, every, threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.summary = usage.refresh(None).summary()
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self.every)

    def stop(self):
        self._stop.set()


def watch(a, out=None):
    out = out or sys.stdout
    if getattr(a, "split", False):
        return split(a, out)
    host = getattr(a, "host", None) or "claude"
    cwd = os.getcwd()
    path, session, link = resolve(a, cwd)
    follow = getattr(a, "follow", False)
    if tui.plain(out):
        return _watch_plain(path, host, cwd, follow, out)
    return _watch_live(path, host, cwd, session, out, link)


def _watch_plain(path, host, cwd, follow, out):
    if not path and not follow:
        out.write("No agent session was found for this directory. Start the agent here, or pass --session.\n")
        return 1
    src = Source(path, host)
    last = None
    try:
        while True:
            if src.path:
                src.poll()
                if src.sub_dir:
                    src.stats.resolve_agents(src.sub_dir)
                text = render_plain(src.stats, cwd, path=src.path)
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


def _watch_live(path, host, cwd, session, out, link=None):
    src = Source(path, host)
    mode = tui.color_mode(out)
    uni = tui.unicode_ok(out)
    motion = Motion()
    feed = LedgerFeed() if host == "claude" else None
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
                if now - last_look > (1 if link else 5) and (link or not src.path):
                    last_look = now
                    p = next_path(src.path, link, cwd, host) if link or not session else find_transcript(cwd, session, host=host)
                    if p and p != src.path:
                        src, motion = Source(p, host), Motion()
                src.poll(now)
                size = shutil.get_terminal_size((80, 24))
                cols, rows = max(30, (size.columns or 80) - 1), max(8, (size.lines or 24) - 1)
                if src.path and os.path.exists(src.path):
                    lines = render_frame(src.stats, cols, rows, now - t0, mode, cwd, uni, now, path=src.path,
                                         motion=motion, ledger=feed.summary if feed else None)
                    if not src.caught_up():
                        lines[2:3] = [Canvas(mode).seg("Reading the session so far...", tui.MUTED)]
                else:
                    lines = [Canvas(mode).seg("NODARIS", tui.ACCENT, bold=True) + "  token monitor", "",
                             "Waiting for the session to start." if link else "Waiting for an agent session in this directory.",
                             "Press q to close this panel."]
                out.write("\x1b[H" + "\n".join(l + "\x1b[0m\x1b[K" for l in lines) + "\x1b[J")
                out.flush()
                fps = 8.0 if src.stats.working(now) else 4.0
                if fd is not None and tui._key_waiting(fd, 1.0 / fps):
                    if tui.read_key(fd) in ("q", "Q", "escape"):
                        break
                elif fd is None:
                    time.sleep(1.0 / fps)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if feed:
            feed.stop()
        out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        out.flush()
        tui._STATE["cursor_hidden"] = False
        signal.signal(signal.SIGTERM, prev_term)
    return 0


def add_arguments(p):
    p.add_argument("--session", help="a session id or transcript path; the newest for this directory by default")
    p.add_argument("--link", help=argparse.SUPPRESS)
    p.add_argument("--host", choices=["claude", "codex"], default="claude")
    p.add_argument("--follow", action="store_true", help="in plain mode, keep printing a summary when it changes")
    p.add_argument("--split", action="store_true", help="open the panel in a side pane (tmux) or show how")
    p.add_argument("--open", action="store_true", help="with --split on macOS, open a new terminal window for it")
