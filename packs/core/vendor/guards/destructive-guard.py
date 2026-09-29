#!/usr/bin/env python3
"""PreToolUse gate: pause or block destructive shell commands and edits to
protected secret paths, before they run.

Source: RoboNuggets video #8 ("DCG" — Destructive Command Guard) and rule #12
in ai-os/second-brain/raw/2026-09-24-agentic/11-robonuggets-videos.md — "a
hook runs regardless of what the model thinks, costs zero tokens, and is the
only thing that reliably stopped a frontier model's stray `rm -rf` in the
wild." Structure and JSON contract mirror ./skillspector-gate.py.

Ground-truth PreToolUse schema verified 2026-09-24 against
https://code.claude.com/docs/en/hooks (the docs.claude.com URL 301s here):
stdin carries session_id/cwd/permission_mode/tool_name/tool_input/...; stdout
JSON is {"hookSpecificOutput": {"hookEventName": "PreToolUse",
"permissionDecision": "allow"|"ask"|"deny", "permissionDecisionReason": str}}.

CRITICAL, load-bearing finding from that doc, specific to this founder's
setup: ~/.claude/settings.json sets "permissions.defaultMode":
"bypassPermissions" as the global default. In bypassPermissions mode (and in
acceptEdits mode), Claude Code IGNORES the permissionDecision field for both
"ask" AND "deny" — the tool call proceeds either way. Exit code 2 is the one
documented mechanism that "blocks the tool call" independent of that
mode-specific override table (blocking message taken from
permissionDecisionReason if present). So:
  - tier "ask" (most of the list below) exits 0 with permissionDecision
    "ask" only — this pauses correctly under "default"/"auto" permission
    mode, but is a no-op (logged, not blocked) under this shop's default
    bypassPermissions mode. That is a deliberate, spec-directed choice: the
    task list below is explicitly two-tier (ask vs. hard deny), and turning
    every "ask" into a hard block would defeat that split and make routine
    work unusable.
  - tier "deny" (rm -rf / and rm -rf ~ and equivalents) exits 2 IN ADDITION
    to the JSON deny, specifically so the two truly catastrophic cases still
    get a real, mode-independent stop even under bypassPermissions.
  - --mode codex always hard-stops (exit 2 + deny), matching
    skillspector-gate.py's own "Codex hooks cannot pause" reasoning. That
    hook has backed its codex deny with exit 2 too since 2026-09-24; before
    that, its JSON-only deny was exactly the override described above.
Practical upshot to flag back to the owner: today, under bypassPermissions,
"ask" is an audit log + best-effort prompt, not a stop. Only the two hard
"deny" cases and Write/Edit's protected-path check are guaranteed to block.
If real friction on the "ask" tier is wanted, permission_mode needs to move
off bypassPermissions (globally or per matcher) — this hook cannot do that
from inside itself.

Shared by both harnesses via --mode (mirrors skillspector-gate.py):
  claude -> tier "ask" pauses (best-effort, see above); tier "deny" hard-stops
  codex  -> everything that would fire always hard-stops (exit 2 + deny)

Performance: pure parsing (tokenize + classify) is well under 50ms — same
budget as skillspector-gate.py's own hook. The few "if cheaply checkable"
git-status/-clean/-branch checks the task spec explicitly allows (git
reset --hard, git clean -fdx, git checkout -- ., git branch -D, git worktree
remove --force) shell out to local, no-network git plumbing with a 2s cap
each; when a check can't complete, the affected case falls back to "ask" per
spec ("if cheaply checkable, else ask"), never to silent allow.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

LOG_PATH = Path(os.environ.get("DESTRUCTIVE_GUARD_LOG") or Path(os.environ.get("NODARIS_HARNESS_HOME") or Path.home() / ".nodaris-harness") / "logs" / "destructive-guard.log")
GIT_TIMEOUT_SECONDS = 2

SAFE_TMP_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/")
SAFE_DIR_COMPONENTS = {
    "node_modules", ".vite", "dist", "build", ".next", ".turbo",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".cache", "coverage", "htmlcov", ".parcel-cache",
}
PROTECTED_BRANCHES = {"main", "master", "prod", "production"}
AIOS_LAUNCHCTL_PREFIX = "com.aios."
OPERATORS = {"&&", "||", ";", "|", "&", "(", ")", "{", "}", ";;", "|&"}

OVERRIDE_RE = re.compile(r"#\s*guard:ok\s*$")
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
ENV_FILE_RE = re.compile(r"^\.env(\.(?!example$|sample$|template$|test$)[^.]+)?$", re.IGNORECASE)
CURL_RE = re.compile(r"\bcurl\b")
PIPE_TO_SHELL_RE = re.compile(r"\|\s*(sudo\s+)?(sh|bash)\b")
DROP_DB_RE = re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE)
REDIRECT_RE = re.compile(r"^(>{1,2}|1>{1,2}|2>{1,2}|&>{1,2})$")

SECRET_KV_RE = re.compile(r"(?i)\b([\w-]*(?:pass(?:word)?|secret|token|apikey|api[_-]?key|credential)[\w-]*)\s*=\s*(\S+)")
LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-/+=]{24,}\b")

FALLBACK_RAW_PATTERNS = [
    (re.compile(r"\brm\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*\s+)+/(?:\s|$)"), "deny", "unparseable command looks like rm -rf /"),
    (re.compile(r"\brm\s+.*-[a-zA-Z]*[rR]"), "ask", "unparseable command contains an rm -r-like pattern"),
    (re.compile(r"\bdd\s+if="), "ask", "unparseable command contains dd"),
    (re.compile(r"\bmkfs\b"), "ask", "unparseable command contains mkfs"),
]


# ---------------------------------------------------------------- plumbing

def read_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def redact(s: str) -> str:
    s = SECRET_KV_RE.sub(lambda m: f"{m.group(1)}=***REDACTED***", s)

    def mask(m: "re.Match") -> str:
        v = m.group(0)
        return v[:4] + "...redacted..." + v[-4:] if len(v) > 12 else "***REDACTED***"

    return LONG_TOKEN_RE.sub(mask, s)


def log_event(mode: str, decision: str, tier: str, reason: str, target: str) -> None:
    try:
        _log_event(mode, decision, tier, reason, target)
    except OSError:
        pass  # a log that cannot be written must never turn a refusal into a crash, which the host reads as allow


def _log_event(mode: str, decision: str, tier: str, reason: str, target: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": mode,
        "decision": decision,
        "tier": tier,
        "reason": reason,
        "command": redact(target)[:200],
    }
    with open(LOG_PATH, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


# ------------------------------------------------------------- tokenizing

def tokenize(command: str):
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return None


def split_segments(tokens):
    segments, cur = [], []
    for t in tokens:
        if t in OPERATORS:
            if cur:
                segments.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segments.append(cur)
    return segments


def basename_cmd(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def strip_wrapper(tokens):
    """Strip env assignments, sudo (+ its flags), a leading alias-escape
    backslash, and unwrap xargs, returning the effective command tokens."""
    t = list(tokens)
    while t and ENV_ASSIGN_RE.match(t[0]):
        t.pop(0)
    while t and t[0] == "sudo":
        t.pop(0)
        while t and t[0].startswith("-") and t[0] != "--":
            if t[0] == "-u" and len(t) > 1:
                t.pop(0)
            t.pop(0)
        if t and t[0] == "--":
            t.pop(0)
    if t and t[0].startswith("\\") and len(t[0]) > 1:
        t[0] = t[0][1:]
    if t and basename_cmd(t[0]) == "xargs":
        t.pop(0)
        while t and t[0].startswith("-"):
            t.pop(0)
    return t


# -------------------------------------------------------------- path help

def resolve_path(p: str, session_cwd: str) -> str:
    if p.startswith("~"):
        p = str(Path.home()) + p[1:]
    elif p.startswith("$HOME"):
        p = str(Path.home()) + p[len("$HOME"):]
    elif p.startswith("${HOME}"):
        p = str(Path.home()) + p[len("${HOME}"):]
    if not os.path.isabs(p):
        p = os.path.join(session_cwd, p)
    return os.path.normpath(p)


def is_catastrophic_rm_target(path: str, session_cwd: str) -> bool:
    p = path.rstrip("/") or "/"
    if p in ("/", "~", "$HOME", "${HOME}"):
        return True
    try:
        if os.path.normpath(resolve_path(path, session_cwd)) == str(Path.home()):
            return True
    except Exception:
        pass
    return False


def is_always_ask_rm_target(path: str, session_cwd: str) -> bool:
    p = path.rstrip("/")
    if p == "..":
        return True
    if p == "*":
        return True
    if p in (".", session_cwd.rstrip("/")):
        if os.path.exists(os.path.join(session_cwd, ".git")):
            return True
    return False


def is_safe_tmp_path(path: str) -> bool:
    for pre in SAFE_TMP_PREFIXES:
        if path.startswith(pre) and len(path) > len(pre):
            return True
    return False


def has_dotdot_component(path: str) -> bool:
    return ".." in path.split("/")


def is_safe_relative_build_dir(path: str) -> bool:
    if path.startswith(("/", "~", "$")):
        return False
    if has_dotdot_component(path):
        return False
    parts = [p for p in path.split("/") if p not in ("", ".")]
    return any(part in SAFE_DIR_COMPONENTS for part in parts)


def is_protected_secret_path(path: str) -> bool:
    p = path.replace("\\", "/")
    home = str(Path.home())
    if p.startswith("~"):
        p = home + p[1:]
    elif p.startswith("$HOME"):
        p = home + p[len("$HOME"):]
    parts = [seg for seg in p.split("/") if seg]
    base = parts[-1] if parts else ""
    if ".ssh" in parts:
        return True
    if ENV_FILE_RE.match(base):
        return True
    if base.endswith(".keychain") or base.endswith(".keychain-db"):
        return True
    if "Keychains" in parts:
        return True
    return False


# ---------------------------------------------------------- git subprocess

def _git(run_dir: str, args):
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True,
            timeout=GIT_TIMEOUT_SECONDS, cwd=run_dir,
        )
    except Exception:
        return None


def git_status_clean(run_dir: str, pathspec: str = None):
    args = ["status", "--porcelain"]
    if pathspec:
        args += ["--", pathspec]
    proc = _git(run_dir, args)
    if proc is None or proc.returncode != 0:
        return None
    return len(proc.stdout.strip()) == 0


def git_clean_would_remove(run_dir: str):
    proc = _git(run_dir, ["clean", "-ndx"])
    if proc is None or proc.returncode != 0:
        return None
    return len(proc.stdout.strip()) > 0


def git_branch_merged(run_dir: str, branch: str):
    proc = _git(run_dir, ["branch", "--merged"])
    if proc is None or proc.returncode != 0:
        return None
    names = {ln.strip().lstrip("* ").strip() for ln in proc.stdout.splitlines()}
    return branch in names


# ------------------------------------------------------------- classifiers
# Each classifier returns None (not in scope) or ("ask"|"deny", reason).

def classify_rm(args, cwd):
    recursive = False
    targets = []
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a == "--":
            continue
        if a.startswith("--"):
            if a in ("--recursive", "--force"):
                if a == "--recursive":
                    recursive = True
            continue
        if a.startswith("-") and len(a) > 1:
            if any(c in "rR" for c in a[1:]):
                recursive = True
            continue
        targets.append(a)
    if not recursive:
        return None
    if not targets:
        return ("ask", "rm -r/-rf with no explicit path argument (likely fed via xargs/stdin)")
    for t in targets:
        if is_catastrophic_rm_target(t, cwd):
            return ("deny", f"rm -r targeting '{t}' — root/home wipe")
    for t in targets:
        if is_always_ask_rm_target(t, cwd):
            return ("ask", f"rm -r targeting '{t}' (parent dir, repo root, or wildcard)")
    for t in targets:
        if not (is_safe_tmp_path(t) or is_safe_relative_build_dir(t)):
            return ("ask", f"rm -r targeting '{t}', outside scratchpad/tmp/build-cache dirs")
    return None


def classify_find(tokens, cwd):
    paths = []
    i = 1
    while i < len(tokens) and not tokens[i].startswith("-"):
        paths.append(tokens[i])
        i += 1
    rest = tokens[i:]
    has_delete = "-delete" in rest
    has_exec_rm = False
    for j, t in enumerate(rest):
        if t in ("-exec", "-execdir") and j + 1 < len(rest) and basename_cmd(rest[j + 1]) == "rm":
            has_exec_rm = True
    if not (has_delete or has_exec_rm):
        return None
    targets = paths or ["."]
    verb = "-delete" if has_delete else "-exec rm"
    for t in targets:
        if is_catastrophic_rm_target(t, cwd):
            return ("deny", f"find {t} ... {verb} — root/home wipe")
    for t in targets:
        if is_always_ask_rm_target(t, cwd) or not (is_safe_tmp_path(t) or is_safe_relative_build_dir(t)):
            return ("ask", f"find {t} ... {verb} recursively deletes matching files")
    return None


def has_force_flag(tokens):
    for t in tokens:
        if t in ("-f", "--force") or t.startswith("--force-with-lease"):
            return True
        if re.match(r"^-[a-zA-Z]{2,}$", t) and "f" in t[1:]:
            return True
    return False


def classify_git(args, cwd):
    if not args:
        return None
    sub, rest = args[0], args[1:]

    if sub == "push":
        if has_force_flag(rest):
            return ("ask", "git push --force* can overwrite remote history")
        for t in rest:
            if t in PROTECTED_BRANCHES:
                return ("ask", f"git push targets protected branch '{t}'")
            if ":" in t:
                left, _, right = t.partition(":")
                if left in PROTECTED_BRANCHES or right in PROTECTED_BRANCHES:
                    return ("ask", f"git push refspec touches protected branch ({t})")
        return None

    if sub == "reset" and "--hard" in rest:
        clean = git_status_clean(cwd)
        if clean is False:
            return ("ask", "git reset --hard would discard uncommitted changes")
        if clean is None:
            return ("ask", "git reset --hard — could not verify the tree is clean")
        return None

    if sub == "clean":
        flags = "".join(t.lstrip("-") for t in rest if t.startswith("-") and not t.startswith("--"))
        has_f = "f" in flags or "--force" in rest
        has_d = "d" in flags
        has_x = "x" in flags
        if has_f and has_d and has_x:
            would = git_clean_would_remove(cwd)
            if would is True:
                return ("ask", "git clean -fdx would remove untracked/ignored files")
            if would is None:
                return ("ask", "git clean -fdx — could not verify nothing would be removed")
        return None

    if sub == "checkout" and "--" in rest:
        idx = rest.index("--")
        paths = rest[idx + 1:] or ["."]
        for p in paths:
            clean = git_status_clean(cwd, p)
            if clean is False:
                return ("ask", f"git checkout -- {p} would discard working-tree changes")
            if clean is None:
                return ("ask", f"git checkout -- {p} — could not verify the tree is clean")
        return None

    if sub == "branch" and any(t == "-D" or (t.startswith("-") and not t.startswith("--") and "D" in t) for t in rest):
        names = [t for t in rest if not t.startswith("-")]
        for name in names:
            merged = git_branch_merged(cwd, name)
            if merged is False:
                return ("ask", f"git branch -D {name} — branch is not fully merged")
            if merged is None:
                return ("ask", f"git branch -D {name} — could not verify the branch is merged")
        return None

    if sub == "worktree" and rest[:1] == ["remove"] and any(t in ("--force", "-f") for t in rest[1:]):
        paths = [t for t in rest[1:] if not t.startswith("-")]
        target = resolve_path(paths[0], cwd) if paths else cwd
        clean = git_status_clean(target)
        if clean is False:
            return ("ask", f"git worktree remove --force {paths[0] if paths else '.'} — uncommitted changes present")
        if clean is None:
            return ("ask", f"git worktree remove --force {paths[0] if paths else '.'} — could not verify the tree is clean")
        return None

    if sub == "stash" and rest[:1] and rest[0] in ("drop", "clear"):
        return ("ask", f"git stash {rest[0]} permanently discards stashed work")

    return None


def classify_docker(args, cwd):
    if len(args) >= 2 and args[0] == "system" and args[1] == "prune":
        return ("ask", "docker system prune removes unused containers/networks/images")
    if len(args) >= 2 and args[0] == "volume" and args[1] in ("rm", "prune"):
        return ("ask", f"docker volume {args[1]} can delete volume data")
    return None


def classify_gcloud(args, cwd):
    if "delete" in args:
        return ("ask", "gcloud ... delete removes a cloud resource")
    return None


def classify_aws(args, cwd):
    for a in args:
        if re.match(r"^delete-[a-z0-9-]+$", a):
            return ("ask", f"aws {a} deletes a cloud resource")
    if "s3" in args and "rm" in args and "--recursive" in args:
        return ("ask", "aws s3 rm --recursive can bulk-delete an S3 prefix")
    return None


def classify_terraform(args, cwd):
    if "destroy" in args:
        return ("ask", "terraform destroy tears down managed infrastructure")
    return None


def classify_kubectl(args, cwd):
    if "delete" in args:
        return ("ask", "kubectl delete removes a cluster resource")
    return None


def classify_dd(args, cwd):
    return ("ask", "dd can overwrite a raw device or file byte-for-byte")


def classify_diskutil(args, cwd):
    if args and args[0].lower().startswith("erase"):
        return ("ask", f"diskutil {args[0]} erases a disk/volume")
    return None


def classify_chmod(args, cwd):
    recursive = any(a in ("-R", "--recursive") for a in args)
    mode_777 = any(a in ("777", "0777") for a in args)
    if recursive and mode_777:
        return ("ask", "chmod -R 777 makes a whole tree world-writable")
    return None


def classify_launchctl(args, cwd):
    if len(args) >= 2 and args[0] in ("remove", "bootout"):
        label = args[1].rsplit("/", 1)[-1]
        if not label.startswith(AIOS_LAUNCHCTL_PREFIX):
            return ("ask", f"launchctl {args[0]} on non-AIOS label '{label}'")
    return None


def classify_dropdb(args, cwd):
    return ("ask", "dropdb permanently deletes a PostgreSQL database")


def classify_security(args, cwd):
    return ("ask", "security touches the keychain")


def classify_secret_redirect(tokens, cwd):
    """echo/cat/... > ~/.ssh/... or >> some.env — a write redirect into a
    protected path from an arbitrary command, not just an editor."""
    for i, t in enumerate(tokens):
        if REDIRECT_RE.match(t) and i + 1 < len(tokens):
            target = tokens[i + 1]
            if is_protected_secret_path(resolve_path(target, cwd)) or is_protected_secret_path(target):
                return ("ask", f"redirect writes into protected path '{target}'")
    return None


CLASSIFIERS = {
    "rm": classify_rm,
    "docker": classify_docker,
    "gcloud": classify_gcloud,
    "aws": classify_aws,
    "terraform": classify_terraform,
    "kubectl": classify_kubectl,
    "dd": classify_dd,
    "diskutil": classify_diskutil,
    "chmod": classify_chmod,
    "launchctl": classify_launchctl,
    "dropdb": classify_dropdb,
    "security": classify_security,
    "git": classify_git,
}


def classify_segment(tokens, cwd):
    redirect_hit = classify_secret_redirect(tokens, cwd)
    eff = strip_wrapper(tokens)
    if not eff:
        return redirect_hit
    cmd = basename_cmd(eff[0])
    args = eff[1:]
    if cmd.startswith("mkfs"):
        return ("ask", "mkfs formats a filesystem, destroying existing data")
    if cmd == "find":
        hit = classify_find(eff, cwd)
        return hit or redirect_hit
    fn = CLASSIFIERS.get(cmd)
    if fn:
        hit = fn(args, cwd)
        if hit:
            return hit
    return redirect_hit


def classify_raw(command):
    if CURL_RE.search(command) and PIPE_TO_SHELL_RE.search(command):
        return ("ask", "curl piped directly into a shell executes unreviewed remote code")
    if DROP_DB_RE.search(command):
        return ("ask", "command text contains DROP DATABASE")
    return None


def classify_edit_path(file_path: str):
    if is_protected_secret_path(file_path):
        return ("ask", f"editing '{file_path}' touches a credential/secret path")
    return None


# ------------------------------------------------------------------- eval

def evaluate(command: str, cwd: str):
    """Returns (hit_or_None, override_present, suppressed_hit_or_None).
    hit and suppressed_hit are (tier, reason) tuples."""
    override = bool(OVERRIDE_RE.search(command))
    working = OVERRIDE_RE.sub("", command).rstrip() if override else command

    hits = []
    r = classify_raw(working)
    if r:
        hits.append(r)

    tokens = tokenize(working)
    if tokens is not None:
        for seg in split_segments(tokens):
            h = classify_segment(seg, cwd)
            if h:
                hits.append(h)
    else:
        for pat, tier, reason in FALLBACK_RAW_PATTERNS:
            if pat.search(working):
                hits.append((tier, reason))
                break

    if not hits:
        return None, override, None

    deny_hits = [h for h in hits if h[0] == "deny"]
    chosen = deny_hits[0] if deny_hits else hits[0]

    if override and chosen[0] == "ask":
        return None, True, chosen
    return chosen, override, None


# ------------------------------------------------------------------- main

DELETE_REASON = re.compile(r"rm -r|rm -rf|-exec rm|-delete|git clean|unparseable command contains an rm")


def reversible_delete_on() -> bool:
    """On when NODARIS_REVERSIBLE_DELETE=1, or when the harness settings say "reversible_delete": true."""
    if os.environ.get("NODARIS_REVERSIBLE_DELETE", "").lower() in ("1", "true", "yes", "on"):
        return True
    home = os.environ.get("NODARIS_HARNESS_HOME") or os.path.join(os.path.expanduser("~"), ".nodaris-harness")
    try:
        with open(os.path.join(home, "settings.json")) as fh:
            return json.load(fh).get("reversible_delete") is True
    except (OSError, ValueError, AttributeError):
        return False


def trash_command() -> str:
    import shutil as _sh
    if _sh.which("nodaris-harness"):
        return "`nodaris-harness trash <paths>` (restore with `nodaris-harness trash --restore <id>`)"
    if os.path.exists("/usr/bin/trash"):
        return "`/usr/bin/trash <paths>` (restore from the Finder Trash)"
    return "`mkdir -p ~/.agent-trash && mv <paths> ~/.agent-trash/`"


def emit(mode: str, tier: str, reason: str, target: str):
    hard = (tier == "deny") or (mode == "codex")
    if not hard and reversible_delete_on() and DELETE_REASON.search(reason):
        msg = (f"destructive-guard: {reason}. Reversible-delete mode is on, so this is not deleted permanently. "
               f"Move it to the trash instead with {trash_command()}. For git clean, list the files with "
               f"`git clean -n` and move those. If the person asked for a permanent delete, add `# guard:ok` at the "
               f"end of the command.")
        log_event(mode, "deny", "reversible", reason, target)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": msg}}))
        print(msg, file=sys.stderr)
        sys.exit(2)
    # Owner decision, 2026-09-24: the "ask" tier kept raising "Allow once" prompts they
    # could not make permanent. It now logs only; the hard denies still block.
    if not hard:
        log_event(mode, "logged", tier, reason, target)
        sys.exit(0)
    decision = "deny"
    log_event(mode, decision, tier, reason, target)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": f"destructive-guard: {reason}",
        }
    }))
    if hard:
        print(f"destructive-guard: {reason}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


def main():
    mode = "claude"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]

    payload = read_payload()
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()

    if tool_name == "Bash":
        command = tool_input.get("command") or ""
        if not command.strip():
            sys.exit(0)
        hit, override, suppressed = evaluate(command, cwd)
        if suppressed:
            log_event(mode, "override", suppressed[0], f"guard:ok override — {suppressed[1]}", command)
            sys.exit(0)
        if hit is None:
            sys.exit(0)
        emit(mode, hit[0], hit[1], command)
        return

    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path") or ""
        if not file_path:
            sys.exit(0)
        hit = classify_edit_path(file_path)
        if hit is None:
            sys.exit(0)
        emit(mode, hit[0], hit[1], file_path)
        return

    sys.exit(0)


if __name__ == "__main__":
    main()
