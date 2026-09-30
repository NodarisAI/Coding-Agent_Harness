"""Rules of engagement as code: classify every tool call as prohibited, consequential or routine.

The class is decided here, from the policy file, never by a model (RoE R-INT-04). The prohibited set is checked
first, then the consequential set; anything else is routine and is recorded. A consequential call runs only when a
person approved that exact call at a terminal (R-CLASS-03, R-APPROVE-01 to 03); each approval is used once.

Approvals: the gate writes a pending record with the literal action and its hash. `nodaris-harness approve HASH`,
run by a person in their own terminal, shows the literal action, reads "yes" from /dev/tty (an agent's shell has no
terminal) and writes a record signed with a key in the harness home. The gate verifies the signature, checks the hash
of the call about to run and marks the record used. The key is readable by the same OS user, so this proves a
person at a terminal only as far as the deny rules keep the agent away from the key; the managed settings step on
each machine is what locks it (blueprint section 5).

Two lighter forms, only for rules the policy marks (policy 0.3, after a teammate's report on 2026-09-30):
  - "ask": in a Claude Code permission mode that shows prompts, the hook asks Claude Code to show its own permission
    dialog instead of refusing, and the person answers there. In bypassPermissions or dontAsk no dialog would appear,
    so the terminal approval stays.
  - "grantable": at the terminal the person may answer "session" instead of "yes". That signs a grant for the rule,
    this session and this repository, valid for GRANT_TTL; the gate honours it without using it up.

Consent overrides (2026-09-30, Varun: the harness is the person's, so they can override any stop once they have
understood it). Every stop, prohibited ones included, writes a signed pending record. The person can approve it at
the terminal as before, or, in Claude Code, answer a question the agent asks with AskUserQuestion. The question text
is written here from the verified pending record, so the person sees the real action and the real reason. The hook
records the answer only when the PreToolUse hook saw the same question go out with no answers pre-filled (the
"asked" marker, bound to the tool_use_id and signed) and the answer names the same code, session and question. Rules
marked "consent": "terminal" protect the approval mechanism itself and are approved only at the terminal.
"""
import fnmatch, hashlib, hmac, json, os, re, secrets, shlex, time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_POLICY = os.path.join(os.path.dirname(os.path.dirname(HERE)), "rules", "policy.json")
HOME = os.environ.get("NODARIS_HARNESS_HOME") or os.path.join(os.path.expanduser("~"), ".nodaris-harness")


def home():
    """The harness home, read at call time so a test or an install can point it elsewhere."""
    return os.environ.get("NODARIS_HARNESS_HOME") or os.path.join(os.path.expanduser("~"), ".nodaris-harness")
SHELL_TOOLS = {"Bash", "shell", "run_shell_command", "exec_command", "bash"}
READ_TOOLS = {"Read", "read_file", "read", "view"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "write_file", "replace", "edit", "write", "apply_patch"}
APPROVAL_TTL = 30 * 60
GRANT_TTL = 12 * 60 * 60
# Programs that put a file's contents into the conversation. Copying, moving or counting a file does not.
SHELL_READERS = {"cat", "head", "tail", "less", "more", "sed", "awk", "grep", "rg", "cut", "sort", "uniq", "strings",
                 "xxd", "od", "hexdump", "jq", "bat", "nl", "tac", "column", "csvlook", "csvcut", "tr", "paste", "diff"}


class Decision:
    def __init__(self, cls, rule_id="", why="", action_hash="", approved=False, ask=False, grantable=False,
                 terminal_only=False):
        self.cls, self.rule_id, self.why, self.action_hash, self.approved = cls, rule_id, why, action_hash, approved
        self.ask, self.grantable, self.terminal_only = bool(ask), bool(grantable), bool(terminal_only)

    def __repr__(self):
        return f"Decision({self.cls}, {self.rule_id})"


def load_policy(path=None):
    path = path or os.environ.get("NODARIS_HARNESS_POLICY") or DEFAULT_POLICY
    with open(path, "rb") as fh:
        raw = fh.read()
    policy = json.loads(raw)
    policy["_sha256"] = hashlib.sha256(raw).hexdigest()
    return policy


def action_hash(tool, tool_input, cwd):
    """The exact call: tool, its arguments (file contents by digest) and the working directory."""
    ti = dict(tool_input or {})
    for k in ("content", "new_string", "old_string", "edits", "patch"):
        if k in ti:
            ti[k] = hashlib.sha256(json.dumps(ti[k], sort_keys=True).encode()).hexdigest()
    blob = json.dumps({"tool": tool, "input": ti, "cwd": os.path.realpath(cwd or ".")}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
WRAPPERS = {"time", "nohup", "command", "exec", "nice", "caffeinate", "stdbuf"}


def strip_heredocs(cmd):
    """Heredoc bodies are data given to a program, not commands; the opening line stays."""
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        tags = [m.group(2) for m in HEREDOC.finditer(line)]
        i += 1
        for tag in tags:
            while i < len(lines) and lines[i].strip() != tag:
                i += 1
            i += 1
    return "\n".join(out)


def mask_quotes(text):
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "Q", text)


def split_segments(text):
    """Split on unquoted ; & && || | and newlines, and add the insides of $( ) as segments of their own."""
    segs, buf, q, i = [], [], None, 0
    while i < len(text):
        c = text[i]
        if q:
            buf.append(c)
            if c == "\\" and q == '"' and i + 1 < len(text):
                buf.append(text[i + 1]); i += 2; continue
            if c == q:
                q = None
        elif c in "'\"":
            q = c; buf.append(c)
        elif c in ";|&\n":
            segs.append("".join(buf)); buf = []
        else:
            buf.append(c)
        i += 1
    segs.append("".join(buf))
    for m in re.finditer(r"\$\(([^()]*)\)|`([^`]*)`", re.sub(r"'[^']*'", "''", text)):  # single quotes keep $( ) literal
        segs.append(m.group(1) or m.group(2) or "")
    return [s.strip().lstrip("({").strip() for s in segs if s.strip()]


def commands(text):
    """Yield (program, argument words, environment assignments) for each simple command in a shell text."""
    for seg in split_segments(text):
        try:
            words = shlex.split(seg, comments=True)
        except ValueError:
            words = seg.split()
        env = []
        while words:
            w = words[0]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
                env.append(w); words = words[1:]
            elif w in WRAPPERS or w == "env":
                words = words[1:]
            elif w == "timeout" and len(words) > 1:
                words = words[2:]
            elif w == "sudo":
                yield "sudo", words[1:], env
                words = [x for x in words[1:] if not x.startswith("-")]
            else:
                break
        if words:
            yield os.path.basename(words[0]), words[1:], env


def git_parts(args):
    i = 0
    while i < len(args) and args[i].startswith("-"):  # git -C dir / -c k=v take a value
        i += 2 if args[i] in ("-C", "-c") else 1
    return (args[i], args[i + 1:]) if i < len(args) else ("", [])


def _git_flag_hit(sub, sargs, git_flags):
    banned = git_flags.get(sub, [])
    for a in sargs:
        if a == "--":
            break
        if a in banned:
            return True
        if "-n" in banned and re.fullmatch(r"-[a-zA-Z]+", a) and "n" in a[1:] and not a.startswith("-m"):
            return True  # a combined short-flag cluster such as -an
    return False


def _protected_push(sargs, protected):
    positional = [a for a in sargs if not a.startswith("-")]
    for ref in positional[1:]:
        target = ref.split(":", 1)[1] if ":" in ref else ref
        target = target.lstrip("+")
        target = target[len("refs/heads/"):] if target.startswith("refs/heads/") else target
        if target in protected:
            return True
    return False


VALUE_FLAGS = {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--json", "-H", "--header", "-F",
               "--form", "-w", "--write-out", "-o", "--output", "-u", "--user", "-e", "--referer", "-A", "--user-agent"}


def _targets(args):
    """The arguments a request is sent to: a URL inside a body, a header or an output option is not a target."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in VALUE_FLAGS:
            skip = True
        elif not re.match(r"^(-d|-H|-F|-w|-o|--data\S*|--header|--form|--json)=?", a) or a.startswith("http"):
            out.append(a)
    return out


def _cmd_hit(entry, prog, args, env):
    if not re.fullmatch(entry["prog"], prog):
        return False
    if "env" in entry and not any(re.search(entry["env"], e) for e in env):
        return False
    joined = " ".join(args)
    if "args" in entry and not re.search(entry["args"], joined):
        return False
    if "needs_args" in entry and not re.search(entry["needs_args"], " ".join(_targets(args))):
        return False
    return True


def _glob_hit(path, patterns):
    p = path.replace(os.sep, "/")
    return any(fnmatch.fnmatch(p, pat) or fnmatch.fnmatch("/" + p.lstrip("/"), pat) for pat in patterns)


def _is_synthetic(path, policy):
    base = os.path.basename(path).lower()
    return any(m in base for m in policy.get("synthetic_markers", []))


PUBLISHING = {("git", "commit"), ("gh", "pr"), ("gh", "issue"), ("gh", "release")}


def _rule_hit(rule, tool, cmd, parsed, bare, path, text, policy):
    if cmd:
        for prog, args, env in parsed:
            if any(_cmd_hit(e, prog, args, env) for e in rule.get("cmd", [])):
                return True
            if prog == "git":
                sub, sargs = git_parts(args)
                if rule.get("git_flags") and _git_flag_hit(sub, sargs, rule["git_flags"]):
                    return True
                if sub in rule.get("git_sub", []):
                    return True
                if rule.get("protected_push") and sub == "push" and _protected_push(sargs, policy.get("protected_branches", [])):
                    return True
        if any(re.search(rx, bare) for rx in rule.get("anywhere", [])):
            return True
        if any(re.search(rx, strip_heredocs(cmd)) for rx in rule.get("path_text", [])):
            return True
        if rule.get("publish_text"):
            publishing = any((prog, (git_parts(args)[0] if prog == "git" else (args[0] if args else ""))) in PUBLISHING
                             for prog, args, env in parsed)
            if publishing and any(re.search(rx, cmd) for rx in rule["publish_text"]):
                return True
    if text and any(re.search(rx, text) for rx in rule.get("write_text", [])):
        return True
    if path and rule.get("paths") and _glob_hit(path, rule["paths"]):
        return True
    if rule.get("phi_read") and tool in READ_TOOLS and path and _glob_hit(path, policy.get("phi_path_markers", [])) \
            and not _is_synthetic(path, policy):
        return True
    if rule.get("phi_read") and cmd:
        for prog, args, env in parsed:
            if prog in SHELL_READERS and any(not a.startswith("-") and _glob_hit(a, policy.get("phi_path_markers", []))
                                             and not _is_synthetic(a, policy) for a in args):
                return True
    return False


def classify(tool, tool_input, cwd, policy=None):
    policy = policy or load_policy()
    ti = tool_input or {}
    cmd = str(ti.get("command") or "") if tool in SHELL_TOOLS else ""
    path = str(ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or "")
    text = " ".join(str(ti.get(k) or "") for k in ("content", "new_string")) if tool in WRITE_TOOLS else ""
    h = action_hash(tool, ti, cwd)
    stripped = strip_heredocs(cmd) if cmd else ""
    parsed = list(commands(stripped)) if cmd else []
    bare = mask_quotes(stripped)
    for cls_name in ("prohibited", "consequential"):
        for rule in policy.get(cls_name, []):
            if _rule_hit(rule, tool, cmd, parsed, bare, path, text, policy):
                light = cls_name == "consequential"
                return Decision(cls_name, rule["id"], rule["why"], h, ask=light and rule.get("ask") is True,
                                grantable=light and rule.get("grantable") is True,
                                terminal_only=rule.get("consent") == "terminal")
    return Decision("routine", "", "", h)


# ---- approvals ------------------------------------------------------------------------------------------------

def _dirs():
    out = {k: os.path.join(home(), k) for k in ("keys", "approvals", "pending", "used")}
    for k, d in out.items():
        os.makedirs(d, mode=0o700, exist_ok=True)
    return out


def _key():
    path = os.path.join(_dirs()["keys"], "approval.key")
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(secrets.token_bytes(32))
    with open(path, "rb") as fh:
        return fh.read()


def _sign(record):
    body = json.dumps({k: record[k] for k in ("hash", "action", "approver", "channel", "created")}, sort_keys=True)
    return hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()


def scope_of(cwd):
    """What a grant covers: the repository containing cwd (its top folder), or cwd itself outside a repository."""
    here = os.path.realpath(cwd or ".")
    probe = here
    while True:
        if os.path.exists(os.path.join(probe, ".git")):
            return probe
        up = os.path.dirname(probe)
        if up == probe:
            return here
        probe = up


ONCE, FOR_SESSION, DECLINE = "Allow once", "Allow for this session", "Do not allow"
CODE_RE = re.compile(r"\bNH-([0-9a-f]{10})\b")
QUESTION_MAX = 1500   # longer actions are approved at the terminal, where the whole action is shown
PENDING_KEYS = ("hash", "rule", "why", "tool", "action", "cwd", "created", "grantable", "session", "scope",
                "terminal_only", "question")


def describe(tool, tool_input):
    """The action in words a person can check: the full command, or the tool and the file it touches."""
    ti = tool_input or {}
    if tool in SHELL_TOOLS:
        return "run the command `" + str(ti.get("command") or "") + "`"
    path = ti.get("file_path") or ti.get("path") or ti.get("notebook_path")
    if path and tool in READ_TOOLS:
        return f"read the file {path}"
    if path and tool in WRITE_TOOLS:
        return f"change the file {path}"
    return f"use the {tool} tool with " + json.dumps(ti, sort_keys=True)


def _question(rec):
    text = (f"The Nodaris harness stopped an action ({rec['rule']}). Reason: {rec['why']} "
            f"The agent wants to {describe(rec['tool'], rec['action'])} in {rec['cwd']}. "
            f"Approval code NH-{rec['hash'][:10]}. Do you allow it?")
    return None if rec.get("terminal_only") or len(text) > QUESTION_MAX else text


def consent_options(rec):
    """The AskUserQuestion payload the agent sends unchanged; the person answers it in the app."""
    opts = [{"label": ONCE, "description": "Run this exact action one time."}]
    if rec.get("grantable"):
        opts.append({"label": FOR_SESSION, "description": "Allow this kind of action in this repository until the "
                                                          "session ends, for up to 12 hours."})
    opts.append({"label": DECLINE, "description": "Keep the action stopped."})
    return {"questions": [{"question": rec["question"], "header": "Approval", "multiSelect": False, "options": opts}]}


def _sign_pending(rec):
    body = json.dumps({k: rec.get(k) for k in PENDING_KEYS}, sort_keys=True)
    return hmac.new(_key(), ("pending:" + body).encode(), hashlib.sha256).hexdigest()


def _prune(folder, age=24 * 60 * 60):
    now = time.time()
    try:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if now - os.path.getmtime(path) > age:
                os.remove(path)
    except OSError:
        pass


def write_pending(decision, tool, tool_input, cwd, session=None):
    """Record a stopped call so a person can approve it, and return the record (with its consent question)."""
    rec = {"hash": decision.action_hash, "rule": decision.rule_id, "why": decision.why, "tool": tool,
           "action": tool_input, "cwd": cwd, "created": time.time(), "grantable": decision.grantable,
           "session": session, "scope": scope_of(cwd), "terminal_only": decision.terminal_only}
    rec["question"] = _question(rec)
    rec["signature"] = _sign_pending(rec)
    folder = _dirs()["pending"]
    _prune(folder)
    with open(os.path.join(folder, decision.action_hash + ".json"), "w") as fh:
        json.dump(rec, fh, indent=1)
    return rec


def _load_pending(path):
    """A pending record the harness wrote, unchanged, describing the call its hash names; otherwise None."""
    try:
        rec = json.load(open(path))
        good = hmac.compare_digest(str(rec.get("signature", "")), _sign_pending(rec))
        same = action_hash(rec["tool"], rec["action"], rec["cwd"]) == rec["hash"]
    except (ValueError, KeyError, OSError, TypeError):
        return None
    return rec if good and same else None


def _write_approval(rec, approver, channel):
    out = {"hash": rec["hash"], "action": rec["action"], "approver": approver, "channel": channel, "created": time.time()}
    out["signature"] = _sign(out)
    with open(os.path.join(_dirs()["approvals"], rec["hash"] + ".json"), "w") as fh:
        json.dump(out, fh, indent=1)


def _write_grant(rec, approver, channel):
    grant = {"rule": rec["rule"], "session": rec["session"], "scope": rec["scope"], "approver": approver,
             "channel": channel, "created": time.time()}
    grant["signature"] = _sign_grant(grant)
    name = "grant-" + hashlib.sha256(json.dumps([grant["rule"], grant["session"], grant["scope"]]).encode()).hexdigest()[:24]
    with open(os.path.join(_dirs()["approvals"], name + ".json"), "w") as fh:
        json.dump(grant, fh, indent=1)


def approve(action_hash_value, approver, answer_reader):
    """Called by the CLI in a person's terminal. answer_reader shows the literal action and returns the typed answer."""
    d = _dirs()
    pending = os.path.join(d["pending"], action_hash_value + ".json")
    if not os.path.exists(pending):
        return False, "no pending action with that hash"
    rec = _load_pending(pending)
    if rec is None:
        return False, "not approved: the pending record was changed or does not match its action; repeat the call"
    answer = (answer_reader(rec) or "").strip().lower()
    if answer == "session":
        if not (rec.get("grantable") and rec.get("session")):
            return False, "not approved: this action can only be approved once; type yes"
        _write_grant(rec, approver, "terminal")
        os.remove(pending)
        return True, f"approved for this session: {rec['rule']} actions in {rec['scope']}"
    if answer != "yes":
        return False, "not approved"
    _write_approval(rec, approver, "terminal")
    os.remove(pending)
    return True, "approved once"


def _asked_path(tool_use_id):
    return os.path.join(_dirs()["pending"], "asked-" + hashlib.sha256(str(tool_use_id).encode()).hexdigest()[:24] + ".json")


def _sign_asked(rec):
    body = json.dumps({k: rec.get(k) for k in ("tool_use_id", "session", "questions", "created")}, sort_keys=True)
    return hmac.new(_key(), ("asked:" + body).encode(), hashlib.sha256).hexdigest()


def mark_asked(tool_use_id, session, questions):
    """Called by the PreToolUse hook when an approval question goes out with no answers filled in."""
    if not tool_use_id or not session:
        return False
    rec = {"tool_use_id": str(tool_use_id), "session": session, "questions": list(questions), "created": time.time()}
    rec["signature"] = _sign_asked(rec)
    with open(_asked_path(tool_use_id), "w") as fh:
        json.dump(rec, fh)
    return True


def _take_asked(tool_use_id, session):
    path = _asked_path(tool_use_id)
    try:
        rec = json.load(open(path))
        os.remove(path)
        good = hmac.compare_digest(str(rec.get("signature", "")), _sign_asked(rec))
    except (ValueError, KeyError, OSError, TypeError):
        return None
    fresh = time.time() - float(rec.get("created") or 0) <= APPROVAL_TTL
    return rec["questions"] if good and fresh and rec.get("session") == session else None


def record_consent(tool_use_id, session, answers, approver):
    """Turn the person's answers to approval questions into approvals. Returns one line per question it handled.

    Only questions the PreToolUse hook saw go out unanswered count, and only when the question is exactly the one
    the harness wrote for that code in this session."""
    if not isinstance(answers, dict):
        return []
    asked = _take_asked(tool_use_id, session)
    out = []
    for question, label in answers.items():
        m = CODE_RE.search(str(question))
        if not m:
            continue
        code = "NH-" + m.group(1)
        if asked is None or question not in asked:
            out.append(f"{code}: nothing was approved, because the harness did not see this question asked unanswered.")
            continue
        folder = _dirs()["pending"]
        names = [n for n in os.listdir(folder) if n.startswith(m.group(1)) and not n.startswith("asked-")]
        rec = _load_pending(os.path.join(folder, names[0])) if len(names) == 1 else None
        if (rec is None or rec.get("session") != session or rec.get("question") != question
                or time.time() - float(rec.get("created") or 0) > APPROVAL_TTL):
            out.append(f"{code}: nothing was approved, because the question does not match a current stopped action "
                       f"in this session.")
            continue
        path = os.path.join(folder, names[0])
        if label == ONCE:
            _write_approval(rec, approver, "app")
            os.remove(path)
            out.append(f"{code}: the person allowed this action once. Repeat exactly the same call now.")
        elif label == FOR_SESSION and rec.get("grantable") and rec.get("session"):
            _write_grant(rec, approver, "app")
            os.remove(path)
            out.append(f"{code}: the person allowed {rec['rule']} actions in {rec['scope']} for this session. "
                       f"Repeat the call now.")
        elif label == DECLINE:
            os.remove(path)
            out.append(f"{code}: the person did not allow this action. Do not try it another way; continue without it.")
        else:
            out.append(f"{code}: the answer was not one of the offered choices, so nothing was approved.")
    return out


def _sign_grant(grant):
    body = json.dumps({k: grant[k] for k in ("rule", "session", "scope", "approver", "channel", "created")}, sort_keys=True)
    return hmac.new(_key(), ("grant:" + body).encode(), hashlib.sha256).hexdigest()


def has_grant(rule_id, session, cwd):
    """True when a signed, unexpired grant covers this rule, session and repository. A grant is not used up."""
    if not session:
        return False
    d = _dirs()
    name = "grant-" + hashlib.sha256(json.dumps([rule_id, session, scope_of(cwd)]).encode()).hexdigest()[:24]
    try:
        rec = json.load(open(os.path.join(d["approvals"], name + ".json")))
        good = hmac.compare_digest(str(rec.get("signature", "")), _sign_grant(rec))
    except (ValueError, KeyError, OSError, TypeError):
        return False
    return (good and rec.get("rule") == rule_id and rec.get("session") == session and rec.get("scope") == scope_of(cwd)
            and time.time() - float(rec.get("created") or 0) <= GRANT_TTL)


def consume_approval(action_hash_value):
    """True when a valid, unused, unexpired approval for exactly this call exists; it is then marked used."""
    d = _dirs()
    path = os.path.join(d["approvals"], action_hash_value + ".json")
    if not os.path.exists(path) or os.path.exists(os.path.join(d["used"], action_hash_value + ".json")):
        return False
    try:
        rec = json.load(open(path))
        good = hmac.compare_digest(rec.get("signature", ""), _sign(rec)) and rec.get("hash") == action_hash_value
    except (ValueError, KeyError, OSError):
        return False
    if not good or time.time() - rec.get("created", 0) > APPROVAL_TTL:
        return False
    rec["used"] = time.time()
    with open(os.path.join(d["used"], action_hash_value + ".json"), "w") as fh:
        json.dump(rec, fh, indent=1)
    os.remove(path)
    return True
