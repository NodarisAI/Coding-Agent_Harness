#!/usr/bin/env python3
"""PreToolUse secret guard for Claude Code and Codex.

  PreToolUse  Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob  python3 secret-guard.py --mode claude|codex

Blocks a tool call that would bring a secret into the conversation or copy it somewhere:
credential stores (SSH keys, cloud and GitHub credentials, the API key file, launch profiles,
the keychain), .env files, shell commands that print a secret environment variable or the
whole environment, and adding a .env file to git. Programs that only load a .env file
(`--env-file .env`, `source .env && cmd`) are allowed, and templates such as .env.example
are always allowed.

It also guards the push approval gate. It refuses a push that would skip the per-push approval
(the real git binary, --no-verify, a hooksPath override, PATH tricks, or a push, merge or release
through the GitHub API), and any change to the gate itself: the approvals, the hooks, the git
shim, git configuration, the approval bot and its signing key. Reading them stays allowed.

It also guards itself. The safety hooks (this guard, the download scanner, the destructive-command
guard, the prompt shaper and the PHI gate) can be read and run but never changed by an agent; a new
version goes to ~/.claude/hooks-staging/ and the owner installs it. settings.json and the Codex hooks
file stay editable through the file tools, as long as every safety hook and deny rule survives the
edit and nothing sets disableAllHooks. An agent may not start another agent without its hooks.

This is the hook half of the rule. The permission deny rules in settings.json cover the
file tools even if this script fails. Shell coverage is pattern matching: it stops the
ordinary ways a secret gets printed, not a determined attempt to hide one; the OS sandbox
(`sandbox.filesystem.denyRead`) is the planned enforcement for that.

A denial exits 2 with the reason on stderr and a deny decision on stdout, which both hosts
honour. The log records the tool and the rule, never the command or the path.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time

H = os.path.expanduser("~")
LOG_PATH = os.environ.get("SECRET_GUARD_LOG") or os.path.join(os.environ.get("NODARIS_HARNESS_HOME") or os.path.join(os.path.expanduser("~"), ".nodaris-harness"), "logs", "secret-guard.jsonl")

RULES = {
    "key-store": "a credential store (SSH keys, cloud or GitHub credentials, the API key file, "
                 "launch profiles or the keychain)",
    "env-file": "a .env secrets file",
    "env-overwrite": "an existing .env secrets file, which would be overwritten",
    "secret-env-var": "an environment variable that holds a secret",
    "env-dump": "the whole environment, which holds API keys",
    "keychain-secret": "a password stored in the macOS keychain",
    "secret-into-git": "a secrets file, which would be committed to git",
    "guard-error": "what looks like a secret (the guard could not parse the call, so it refused)",
    "push-gate": "the push approval gate (its approvals, hooks, git shim, git configuration, approval bot "
                 "or signing key)",
    "push-bypass": "the per-push approval (through the real git binary, --no-verify, a hooksPath override, "
                   "a pushing alias, a changed environment, or a push, merge or release through the GitHub API)",
    "guard-self": "the safety guards themselves (the secret, push, PHI, download and destructive-command "
                  "gates, the settings that switch them on, or an agent started without them)",
}
PUSH_RULES = ("push-gate", "push-bypass")

KEY_DIRS = (".ssh", ".aws", ".gnupg", ".config/gh", ".config/gcloud", ".azure", ".kube", ".claude/profiles",
            "Library/Keychains", ".password-store", ".docker", ".config/aios-push-approval")
KEY_FILES = (".claude/api-keys.json", ".claude/.credentials.json", ".netrc", ".npmrc", ".pypirc",
             ".git-credentials", ".pgpass", ".config/prompt-shaper/.env")
KEY_NAME = re.compile(r"^(?:id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?|.*\.(?:pem|p12|pfx|jks|keystore)|\.git-credentials"
                      r"|\.netrc|\.pgpass|api-keys\.json)$")
ENV_NAME = re.compile(r"^\.env(?:\.[A-Za-z0-9_-]+)*$|^\.envrc$")
TEMPLATE = re.compile(r"\.(?:example|sample|template|dist)$")

_STORES = "|".join(re.escape(x) for x in KEY_DIRS + KEY_FILES if x != ".ssh") + r"|\.ssh/(?!config\b|known_hosts\b|authorized_keys\b|[\w.-]+\.pub\b)[\w.*-]+"
_HOME = r"(?:~/|\$HOME/|\$\{HOME\}/|" + re.escape(H) + r"/)"
KEY_MENTION = re.compile(r"(?:^|[\s'\"=:(,<>])" + _HOME + r"?(?:" + _STORES + r")(?=$|[/\s'\"`;|&)<>,])"
                         r"|api-keys\.json|\bid_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?(?!\.pub)\b")
ENV_MENTION = re.compile(r"(?:^|[\s'\"/=:(,\[<>])(\.env(?:\.[A-Za-z0-9_-]+)*|\.envrc)(?=$|[\s'\"`;|&)<>,\]])")
KEYCHAIN = re.compile(r"\bsecurity\s+(?:find-(?:generic|internet)-password\b[^;&|\n]*\s-[a-zA-Z]*[wg]\b|dump-keychain\b)")

DISPLAY = {
    "cat", "bat", "batcat", "less", "more", "most", "head", "tail", "grep", "egrep", "fgrep", "zgrep", "rg", "ag",
    "ack", "awk", "gawk", "mawk", "nawk", "sed", "gsed", "cut", "sort", "uniq", "strings", "xxd", "od", "hexdump",
    "base64", "base32", "nl", "tac", "rev", "paste", "column", "jq", "yq", "tr", "diff", "colordiff", "cmp", "comm",
    "scp", "curl", "wget", "nc", "ncat", "socat", "ssh", "openssl", "gpg", "tar", "zip", "gzip",
    "bzip2", "xz", "zstd", "7z", "pbcopy", "look", "split", "dd", "iconv", "expand", "unexpand", "fold", "fmt", "pr",
    "vis", "fzf", "view", "mail", "sendmail", "http", "https", "xh", "aws", "gsutil", "gcloud", "az", "gh", "sponge",
}
INTERPRETERS = {"python", "python3", "node", "nodejs", "ruby", "perl", "php", "deno", "bun", "osascript", "pwsh",
                "lua", "Rscript"}
INLINE_FLAGS = {"-c", "-e", "-p", "-E", "--eval", "--print", "-r"}
WRAPPERS = {"sudo", "doas", "env", "command", "exec", "nohup", "time", "nice", "xargs", "caffeinate", "stdbuf",
            "timeout", "gtimeout"}
# Options of a wrapper that take a value, so `timeout 10 git push` or `sudo -u me git push` find the git.
WRAPPER_VALUE_OPTS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group", "--chdir", "--host",
             "--prompt", "--role", "--type", "--other-user", "--command-timeout", "--close-from"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-P", "--unset", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "xargs": {"-n", "-I", "-L", "-P", "-s", "-E", "-d", "-J", "-R", "-S", "--max-args", "--replace", "--max-lines",
              "--max-procs", "--max-chars", "--eof", "--delimiter"},
    "caffeinate": {"-t", "-w"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "gtimeout": {"-s", "-k", "--signal", "--kill-after"},
    "exec": {"-a"},
}
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
DUMP_ALONE = {"printenv", "env", "set", "export", "declare", "typeset"}
SECRET_WORDS = {"KEY", "KEYS", "TOKEN", "TOKENS", "SECRET", "SECRETS", "PASSWORD", "PASSWD", "PASSPHRASE",
                "CREDENTIAL", "CREDENTIALS", "AUTH", "APIKEY", "PAT"}
VAR_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([^\s<>;&|()'\"]+))")
SSH_PUBLIC = re.compile(r"^(?:config|known_hosts(?:\.old)?|authorized_keys|[^/]+\.pub)$")
COPY = {"cp", "rsync", "mv", "ln", "install", "ditto"}
SHELLS = {"bash", "sh", "zsh", "fish", "dash", "ksh"}
SHELL_WORDS = {"then", "else", "elif", "do", "if", "while", "until", "!", "{", "}", "time"}
GUARDED = {"Bash", "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob"}

# The push approval gate: what an agent may read but never change, and how a push could skip it.
SHIM = os.path.join(H, ".local", "bin", "git")
GATE_HOME = (".git-approvals", ".git-hooks", ".local/bin/git", ".gitconfig", ".config/git/config",
             ".config/aios-push-approval", "Library/LaunchAgents/com.aios.push-approval-bot.plist",
             "ai-os/scripts/push-approval-bot.py", "ai-os/scripts/admin")
GATE_ABS = ("/Library/Application Support/AIOS", "/Library/LaunchDaemons/com.aios.push-approval-bot.plist",
            "/Users/Shared/aios-push")
GATE_MENTION = re.compile(
    r"\.git-approvals|\.git-hooks\b|\.local/bin/git(?![\w.-])|\.gitconfig\b|\.config/git/config\b"
    r"|aios-push-approval|Application Support/AIOS\b|/Users/Shared/aios-push|com\.aios\.push-approval-bot"
    r"|push-approval-bot\.py|push_approval\.py|push-approval-service\.sh")
REAL_GIT = re.compile(r"(?:^|/)(?:s?bin|git-core)/git$")
GATE_READ_ONLY = {"cat", "head", "tail", "less", "more", "ls", "stat", "wc", "grep", "egrep", "fgrep", "rg",
                  "diff", "cmp", "file", "jq", "shasum", "md5", "sha256sum", "bat", "tree", "readlink",
                  "realpath", "test", "[", "echo", "printf", "cut", "sort", "uniq", "nl", "od", "xxd",
                  "strings", "column"}
WRITE_HINT = re.compile(r"""['"](?:[wax]|[rwa]\+|[wax]b|[rwa]b\+)['"]|write|unlink|remove|rename|replace\(|shutil"""
                        r"|subprocess|system|popen|chmod|chown|touch|mkdir|rmdir|symlink|link\(|truncate|exec"
                        r"|eval|__import__|importlib|getattr|mode\s*=", re.I)
GATE_COPY_OUT = {"cp", "rsync", "ditto", "ln"}
READ_GIT = {"diff", "log", "show", "status", "blame", "add", "commit", "ls-files", "grep"}
GIT_VALUE_OPTS = {"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--attr-source"}
GH_WRITE_ENDPOINT = re.compile(r"(?:^|/)repos/[^/\s]+/[^/\s]+/(?:git/(?:refs|commits|trees|blobs|tags)\b|contents/"
                               r"|merges\b|pulls/\d+/(?:merge|update-branch)\b|merge-upstream\b"
                               r"|branches/[^/\s]+/rename\b)")
GH_WRITE_MUTATION = re.compile(r"\b(?:createCommitOnBranch|updateRefs?|createRef|deleteRef|mergePullRequest"
                               r"|mergeBranch|enablePullRequestAutoMerge)\b")
REDIRECT_OP = re.compile(r"^(?:\d*>>?|&>>?|>\|)$")
# A push must see the same git, home and configuration as the shim: these variables change which git
# runs, which configuration it reads, or which python the pre-push hook starts.
PUSH_ENV = re.compile(r"^(?:PATH|HOME|XDG_CONFIG_HOME|DEVELOPER_DIR|SDKROOT|GIT_[A-Z_]*)$")
# git subcommands that cannot push. The real git binary may run these; anything else, including an
# alias, may push without the shim, so it is refused.
REAL_GIT_SAFE = {"status", "log", "diff", "show", "rev-parse", "rev-list", "ls-files", "ls-tree", "ls-remote",
                 "cat-file", "blame", "branch", "tag", "fetch", "pull", "clone", "add", "commit", "checkout",
                 "switch", "restore", "reset", "stash", "merge", "cherry-pick", "remote", "init", "worktree",
                 "describe", "shortlog", "grep", "version", "help", "config", "for-each-ref", "show-ref",
                 "symbolic-ref", "merge-base", "name-rev", "reflog", "count-objects", "check-ignore",
                 "check-attr", "apply", "format-patch", "mv", "rm", "archive", "var", "whatchanged", "cherry"}
PUSH_SUBS = {"push", "send-pack", "http-push"}
PUSH_WORDS = re.compile(r"(?<![\w-])push(?![\w-])|send-pack|http-push|--no-verify|hooks-?path|include|^\s*[!-]",
                        re.I)
PUSH_CODE = re.compile(r"--no-verify|hooks-?path|send-pack|http-push|(?:s?bin|git-core)/git\b[^\n]*\bpush\b", re.I)
CONFIG_WRITE_OPTS = {"--add", "--unset", "--unset-all", "--replace-all", "--rename-section", "--remove-section",
                     "-e", "--edit"}
CONFIG_VALUE_OPTS = {"-f", "--file", "--blob", "--type", "--default", "--comment", "--value"}
CONFIG_WRITE_SUBS = {"set", "unset", "rename-section", "remove-section", "edit"}

# The safety hooks themselves: readable and runnable, never changed by an agent.
GUARD_FILES = (".claude/hooks/secret-guard.py", ".claude/hooks/skillspector-gate.py",
               ".claude/hooks/destructive-guard.py", ".claude/hooks/prompt-shaper.py",
               ".claude/hooks/lib/phi_gate.py", ".claude/hooks/lib/phi_guard.py",
               ".claude/hooks/lib/phi_guard.SOURCE.json", ".claude/hooks/lib/jev_client.py")
# Settings that switch the hooks on: editable with the file tools while every safety entry survives.
SETTINGS_FILES = (".claude/settings.json", ".codex/hooks.json")
SAFETY_HOOKS = ("secret-guard.py", "skillspector-gate.py", "destructive-guard.py")
GUARD_MENTION = re.compile(
    r"secret-guard\.py|skillspector-gate\.py|destructive-guard\.py|prompt-shaper\.py|phi_gate\.py"
    r"|phi_guard\.(?:py|SOURCE\.json)|jev_client\.py|\.claude/settings\.json|\.codex/hooks\.json"
    r"|disableAllHooks")
SEARCH_ONLY = {"grep", "egrep", "fgrep", "rg", "cat", "head", "tail", "less", "jq"}
# Outside the protected folders a file is protected only when named with its folder, so a scratch copy
# called push_approval.py or a project's own .claude/settings.json is not mistaken for the real one.
GATE_PATH_MENTION = re.compile(
    r"\.git-approvals|\.git-hooks\b|\.local/bin/git(?![\w.-])|\.gitconfig\b|\.config/git/config\b"
    r"|aios-push-approval|Application Support/AIOS\b|/Users/Shared/aios-push|com\.aios\.push-approval-bot"
    r"|scripts/push-approval-bot\.py|scripts/admin\b")
GUARD_PATH_MENTION = re.compile(
    r"\.claude/hooks/(?:lib/)?(?:secret-guard|skillspector-gate|destructive-guard|prompt-shaper|phi_gate"
    r"|phi_guard|jev_client)\b|(?:~|\$HOME|\$\{HOME\}|" + re.escape(H) + r")/\.(?:claude/settings\.json"
    r"|codex/hooks\.json)|disableAllHooks")
GUARD_BARE_MENTION = re.compile(GUARD_MENTION.pattern + r"|\bsettings\.json\b|\bhooks\.json\b")
GATE_AREAS = (".git-hooks", ".git-approvals", ".local/bin", ".config/aios-push-approval", ".config/git",
              "ai-os/scripts")
GATE_ABS_AREAS = ("/Library/Application Support/AIOS", "/Users/Shared/aios-push")
GUARD_AREAS = (".claude", ".codex")
# Program text that runs commands; a push word in plain data (a JSON note, a README) is not a push.
EXEC_HINT = re.compile(r"subprocess|os\.(?:system|exec\w*|spawn\w*|popen)|popen|child_process|execSync|spawnSync"
                       r"|execFile|pty\.spawn|getoutput|Runtime\.getRuntime")
# A heredoc program that opens a secrets file by name, such as open('.env') or readFileSync('.env').
# load_dotenv is left out on purpose: loading a .env into the environment is how a program should get it.
FILE_READ = re.compile(r"""(?:open|readFileSync|readFile|dotenv_values|file_get_contents|fopen|File\.read|IO\.read"""
                       r"""|expanduser|expandvars)\s*\("""
                       r"""\s*(?:[\w.]+\(\s*)*[rbfu]{0,2}(['"])([^'"\n]+)\1(?!\)?\s*,\s*[rbfu]{0,2}['"][wax])"""
                       r"""|Path\(\s*[rbfu]{0,2}(['"])([^'"\n]+)\3\s*\)\.read_(?:text|bytes)""")
# A short string with no spaces in a program is a candidate path; a sentence that names a file is not.
PATH_LITERAL = re.compile(r"""(['"])([^'"\s]{1,300})\1""")
_CTX = {"gate": False, "guard": False}
# Flags that start an agent without the user settings, and so without these hooks. Permission bypass
# alone keeps the hooks (a hook's exit 2 still blocks), so it is not listed.
AGENT_UNGUARDED = {"--bare", "--settings", "--setting-sources", "--dangerously-bypass-approvals-and-sandbox",
                   "--yolo"}
REDIRECT_JOINED = re.compile(r"^(?:\d*>>?|&>>?|>\|)(?!&)(\S+)$")


def is_env_name(name: str) -> bool:
    return bool(ENV_NAME.match(name)) and not TEMPLATE.search(name)


def is_secret_var(name: str) -> bool:
    return any(part in SECRET_WORDS for part in name.upper().split("_"))


def env_mentions(text: str) -> bool:
    return any(not TEMPLATE.search(m.group(1)) for m in ENV_MENTION.finditer(text))


def classify_path(path: str) -> str | None:
    candidates = {path}
    try:
        candidates.add(os.path.realpath(path))
    except (OSError, ValueError):
        pass
    for cand in candidates:
        base = os.path.basename(cand)
        if cand.startswith(H + os.sep):
            rel = cand[len(H) + 1:]
            if rel.startswith(".ssh/") and SSH_PUBLIC.match(rel[5:]):
                continue  # ssh config, known_hosts and public keys hold no secret
            if any(rel == d or rel.startswith(d + "/") for d in KEY_DIRS if d != ".ssh") or rel in KEY_FILES:
                return "key-store"
            if rel.startswith(".ssh/"):
                return "key-store"
        if KEY_NAME.match(base):
            return "key-store"
        if is_env_name(base):
            return "env-file"
    return None


def is_gate_path(path: str) -> bool:
    """True for the push gate's own files: the approvals, hooks, shim, git config, bot and its key."""
    candidates = {path}
    try:
        candidates.add(os.path.realpath(path))
    except (OSError, ValueError):
        pass
    roots = [os.path.join(H, rel) for rel in GATE_HOME] + list(GATE_ABS)
    return any(c == r or c.startswith(r + "/") for c in candidates for r in roots)


def _under(path: str, rels) -> bool:
    candidates = {path}
    try:
        candidates.add(os.path.realpath(path))
    except (OSError, ValueError):
        pass
    roots = [os.path.join(H, rel) for rel in rels]
    return any(c == r or c.startswith(r + "/") for c in candidates for r in roots)


def is_guard_file(path: str) -> bool:
    """True for a safety hook's own code."""
    return _under(path, GUARD_FILES)


def is_settings_file(path: str) -> bool:
    return _under(path, SETTINGS_FILES)


def is_guard_path(path: str) -> bool:
    return is_guard_file(path) or is_settings_file(path)


def absolute(path, cwd: str) -> str:
    p = re.sub(r"^(?:\$HOME|\$\{HOME\})(?=/|$)", H, str(path))
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return os.path.normpath(p)


def token_rule(token: str, cwd: str) -> str | None:
    """key-store or env-file when a shell word names one; unknown variables are judged by the file name."""
    if not token or token.startswith("-"):
        return None
    if "@" in token:  # curl -F file=@.env, --data-binary @.env
        token = token.split("=@")[-1].rsplit("@", 1)[-1] if token.startswith("@") or "=@" in token else token
    if re.match(r"^(?:~|\$HOME|\$\{HOME\}|/)", token) or "/" not in token or token.startswith("."):
        rule = classify_path(absolute(token, cwd))
        if rule:
            return rule
    base = os.path.basename(token.rstrip("/"))
    return "env-file" if is_env_name(base) else None


def strip_heredocs(command: str) -> str:
    return split_heredocs(command)[0]


def split_heredocs(command: str):
    """(the command without heredoc bodies, [(the line that opened a heredoc, its body)])."""
    out, bodies, waiting, opener, body = [], [], None, "", []
    for line in command.split("\n"):
        if waiting is not None:
            if line.strip() == waiting:
                bodies.append((opener, "\n".join(body)))
                waiting, body = None, []
            else:
                body.append(line)
            continue
        out.append(line)
        m = HEREDOC.search(line)
        if m:
            waiting, opener = m.group(1) or m.group(2) or m.group(3), line
    if waiting is not None:
        # Never closed: judge the swallowed lines as commands too, in case this parse and the shell's differ.
        bodies.append((opener, "\n".join(body)))
        out.extend(body)
    return "\n".join(out), bodies


def extract_substitutions(text: str):
    """(outer text with a placeholder per substitution, [inner command texts]); quote-aware and nested."""
    out, inners, i, n, q = [], [], 0, len(text), None
    while i < n:
        c = text[i]
        if q == "'":
            out.append(c)
            q = None if c == "'" else q
            i += 1
        elif c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
        elif text.startswith("$(", i) and not text.startswith("$((", i):
            j, depth, qq = i + 2, 1, None
            while j < n and depth:
                cj = text[j]
                if qq == "'":
                    qq = None if cj == "'" else qq
                elif cj == "\\":
                    j += 1
                elif text.startswith("$(", j):
                    depth += 1
                    j += 1
                elif qq == '"':
                    qq = None if cj == '"' else qq
                elif cj in "'\"":
                    qq = cj
                elif cj == "(":
                    depth += 1
                elif cj == ")":
                    depth -= 1
                j += 1
            inners.append(text[i + 2:j - 1] if depth == 0 else text[i + 2:])
            out.append(" __SUBST__ ")
            i = j
        elif c == "`":
            j = text.find("`", i + 1)
            j = n if j < 0 else j
            inners.append(text[i + 1:j])
            out.append(" __SUBST__ ")
            i = j + 1
        elif q == '"':
            out.append(c)
            q = None if c == '"' else q
            i += 1
        elif c in "'\"":
            q = c
            out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out), inners


def split_simple(text: str):
    """[(simple command, separator after it)], split outside quotes."""
    segs, cur, q, i, n = [], [], None, 0, len(text)
    while i < n:
        c = text[i]
        if q:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                cur.append(text[i + 1])
                i += 1
            elif c == q:
                q = None
            i += 1
        elif c == "\\" and i + 1 < n:
            cur.append(text[i:i + 2])
            i += 2
        elif c in "'\"":
            q = c
            cur.append(c)
            i += 1
        elif text.startswith("&&", i) or text.startswith("||", i):
            segs.append(("".join(cur), text[i:i + 2]))
            cur = []
            i += 2
        elif c in "&|" and ((i and text[i - 1] in "<>") or text[i + 1:i + 2] == ">"):
            cur.append(c)  # part of a redirection such as >&2, &>file or >|file
            i += 1
        elif c in ";|&\n()":
            segs.append(("".join(cur), c))
            cur = []
            i += 1
        else:
            cur.append(c)
            i += 1
    segs.append(("".join(cur), ""))
    return segs


def segments(text: str, depth: int = 0):
    outer, inners = extract_substitutions(text)
    segs = split_simple(outer)
    if depth < 6:
        for inner in inners:
            segs += segments(inner, depth + 1)
    return segs


def tokens_of(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return segment.split()


def after_wrapper(words: list[str], j: int) -> int:
    """The index after wrapper words[j] and its options, assignments and (for timeout) duration."""
    w = os.path.basename(words[j])
    opts = WRAPPER_VALUE_OPTS.get(w, set())
    j += 1
    while j < len(words):
        a = words[j]
        if a == "--":
            j += 1
            break
        if a in opts:
            j += 2
        elif a.startswith("-") and len(a) > 1 or w == "env" and ASSIGN.fullmatch(a):
            j += 1
        else:
            break
    if w in ("timeout", "gtimeout") and j < len(words):
        j += 1
    return j


def env_split(words: list[str]) -> str | None:
    """The command string given to `env -S`, which env splits and runs."""
    for k, a in enumerate(words):
        if os.path.basename(a) != "env":
            continue
        for m, b in enumerate(words[k + 1:], k + 1):
            if b in ("-S", "--split-string") and m + 1 < len(words):
                return " ".join(words[m + 1:])
            if b.startswith("--split-string="):
                return b.split("=", 1)[1] + " " + " ".join(words[m + 1:])
            if not b.startswith("-") and not ASSIGN.fullmatch(b):
                break
    return None


def resolve_command(words: list[str]):
    """(raw command word, args, the words before it) after assignments, wrappers and shell keywords."""
    j = 0
    while j < len(words):
        w = os.path.basename(words[j])
        if ASSIGN.fullmatch(words[j]) or w in SHELL_WORDS:
            j += 1
        elif w in WRAPPERS:
            j = after_wrapper(words, j)
        else:
            break
    if j >= len(words):
        return None, [], words
    return words[j], words[j + 1:], words[:j]


def command_words(words: list[str]):
    """(command, args) after assignments, wrappers and shell keywords; env-only means `env` with nothing to run."""
    raw, args, pre = resolve_command(words)
    if raw is None:
        return ("env", []) if any(os.path.basename(w) == "env" for w in pre) and env_split(words) is None else (None, [])
    return os.path.basename(raw), args


def shell_inline(args: list[str]) -> str | None:
    """The command string of `bash -c CMD`, including clusters such as -lc or -ec."""
    k, inline = 0, False
    while k < len(args):
        a = args[k]
        if a == "--":
            k += 1
            break
        if a in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            k += 2
        elif a == "--command":
            inline = True
            k += 1
        elif a.startswith("--command="):
            return a.split("=", 1)[1]
        elif a.startswith("--"):
            k += 1
        elif a[:1] in "-+" and len(a) > 1:
            inline = inline or "c" in a[1:]
            k += 1
        else:
            break
    return args[k] if inline and k < len(args) else None


def git_subcommand(args: list[str]):
    """(subcommand, its index, the name=value settings given with -c or --config-env)."""
    configs, k = [], 0
    while k < len(args):
        a = args[k]
        if a in ("-c", "--config-env"):
            configs.append(args[k + 1] if k + 1 < len(args) else "")
            k += 2
        elif a.startswith("--config-env="):
            configs.append(a.split("=", 1)[1])
            k += 1
        elif a in GIT_VALUE_OPTS:
            k += 2
        elif a.startswith("-"):
            k += 1
        else:
            return a, k, configs
    return None, k, configs


def config_writes(args: list[str]) -> bool:
    """True when `git config ARGS` (ARGS after the subcommand) would set or remove a value."""
    if args[:1] and args[0] in CONFIG_WRITE_SUBS:
        return True
    if args[:1] in (["get"], ["list"]):
        return False
    positional, k = 0, 0
    while k < len(args):
        a = args[k]
        if a in CONFIG_WRITE_OPTS:
            return True
        if a in CONFIG_VALUE_OPTS:
            k += 2
            continue
        if a.startswith(("--get", "-l", "--list")):
            return False
        if not a.startswith("-") or positional >= 1:
            positional += 1  # after the name, the next word is the value even when it starts with -
        k += 1
    return positional >= 2


def alias_pushes(name_value: str) -> bool:
    """True for an alias definition (alias.x=VALUE or its value) that could push or skip the hook."""
    value = name_value.split("=", 1)[1] if "=" in name_value else name_value
    return bool(PUSH_WORDS.search(value))


def gh_writes_remote(args: list[str]) -> bool:
    """True when a gh command would change a remote branch without going through git push."""
    if args[:2] in (["pr", "merge"], ["repo", "sync"], ["release", "create"]):
        return True
    if args[:1] != ["api"]:
        return False
    method, fields, endpoint, rest, k = None, False, None, args[1:], 0
    while k < len(rest):
        a = rest[k]
        if a in ("-X", "--method"):
            method = rest[k + 1] if k + 1 < len(rest) else ""
            k += 2
        elif a.startswith("--method=") or (a.startswith("-X") and len(a) > 2):
            method = a.split("=", 1)[1] if a.startswith("--method=") else a[2:]
            k += 1
        elif a in ("-f", "-F", "--field", "--raw-field", "--input"):
            fields = True
            k += 2
        elif a.startswith(("--field=", "--raw-field=", "--input=")):
            fields = True
            k += 1
        elif a in ("-H", "--header", "-q", "--jq", "-t", "--template", "--hostname", "--cache", "-p", "--preview"):
            k += 2
        elif a.startswith("-"):
            k += 1
        else:
            endpoint = a if endpoint is None else endpoint
            k += 1
    if endpoint == "graphql":
        return bool(GH_WRITE_MUTATION.search(" ".join(rest)))
    method = (method or ("POST" if fields else "GET")).upper()
    return method != "GET" and bool(GH_WRITE_ENDPOINT.search(endpoint or ""))


def changes_push_env(pre: list[str]) -> bool:
    """True when the words before a command clear the environment or set a variable a push depends on."""
    wrapper = None
    for w in pre:
        if os.path.basename(w) in WRAPPERS:
            wrapper = os.path.basename(w)
        elif wrapper == "env" and w.startswith("-") and w != "--":
            return True  # env -i, env -u NAME, env - ...
        elif wrapper == "command" and w.startswith("-") and "p" in w:
            return True  # command -p runs git from the default path, not through the shim
        elif ASSIGN.fullmatch(w) and PUSH_ENV.match(w.split("=", 1)[0]):
            return True
    return False


def push_bypass_rule(words: list[str]) -> str | None:
    """push-bypass when this simple command would push, merge or release without the per-push approval."""
    raw, args, pre = resolve_command(words)
    if raw is None:
        return None
    name = os.path.basename(raw)
    if name == "git-push":
        return "push-bypass"
    if name == "xcrun" and "git" in args or raw == "__SUBST__" and args[:1] == ["push"]:
        return "push-bypass"
    if raw.startswith("$") and args[:1] and args[0] in PUSH_SUBS:
        return "push-bypass"  # "$GIT" push: a variable standing in for the git binary
    if name == "git":
        real = raw.startswith(("/", "~", "./", "../")) and REAL_GIT.search(raw) and absolute(raw, "/") != SHIM
        sub, k, configs = git_subcommand(args)
        if any(c.lower().startswith("core.hookspath") for c in configs):
            return "push-bypass"
        if any(c.lower().startswith("alias.") and alias_pushes(c) for c in configs):
            return "push-bypass"
        if any(c.lower().startswith(("include.", "includeif.")) for c in configs):
            return "push-bypass"  # an included file can set core.hooksPath
        rest = args[k + 1:]
        if sub == "config" and config_writes(rest):
            if any("hookspath" in a.lower() for a in rest):
                return "push-bypass"
            for m, a in enumerate(rest):
                if a.lower().startswith("alias.") and m + 1 < len(rest) and alias_pushes(rest[m + 1]):
                    return "push-bypass"
                if a.lower().startswith(("include.", "includeif.")):
                    return "push-bypass"
        if sub in ("send-pack", "http-push"):
            return "push-bypass"
        if sub == "push" and (changes_push_env(pre) or any(len(a) >= 9 and "--no-verify".startswith(a) for a in rest)):
            return "push-bypass"
        via_xargs = any(os.path.basename(w) == "xargs" for w in pre)
        harmless = sub is None and (set(args) <= {"--version", "--help", "-h"} if args else not via_xargs)
        if real and sub not in REAL_GIT_SAFE and not harmless:
            return "push-bypass"  # the real binary skips the shim; push, plumbing and aliases go through git
        return None
    if name == "gh" and gh_writes_remote(args):
        return "push-bypass"
    if name == "hash" and "-p" in args and any(REAL_GIT.search(a) or a.endswith("/git") for a in args):
        return "push-bypass"  # hash -p /usr/bin/git git makes a plain `git` skip the shim
    if name == "alias" and any("=" in a and re.search(r"(?:^|/)git\b|push", a.split("=", 1)[1]) for a in args):
        return "push-bypass"  # alias git=/usr/bin/git, which zsh expands in scripts
    return None


def _push_script_ok(path: str) -> bool:
    hooks = os.path.join(H, ".git-hooks") + "/"
    return path.startswith(hooks) and bool(re.fullmatch(r"test_[\w-]+\.py|push_approval\.py", os.path.basename(path)))


def _hook_script_ok(path: str) -> bool:
    return path.startswith(os.path.join(H, ".claude", "hooks") + "/") and path.endswith(".py")


def mentions() -> tuple:
    """(push gate pattern, guard pattern) for the folder this command runs in."""
    gate = GATE_MENTION if _CTX["gate"] else GATE_PATH_MENTION
    guard = GUARD_BARE_MENTION if _CTX["guard"] else GUARD_PATH_MENTION
    return gate, guard


def set_context(text: str, cwd: str, inherit: bool) -> None:
    """Note whether this command runs, or cds, inside a protected folder, where bare file names count."""
    dirs, here = [cwd or os.getcwd()], cwd or os.getcwd()
    for segment, _ in segments(text):
        words = tokens_of(segment)
        raw, args, _ = resolve_command(words)
        if raw and os.path.basename(raw) in ("cd", "pushd") and args:
            here = absolute(args[-1], here)
            dirs.append(here)
    gate = any(d == H or _under(d, GATE_AREAS) or any(d == a or d.startswith(a + "/") for a in GATE_ABS_AREAS)
               for d in dirs)
    guard = any(d == H or _under(d, GUARD_AREAS) for d in dirs)
    if inherit:
        _CTX["gate"] = _CTX["gate"] or gate
        _CTX["guard"] = _CTX["guard"] or guard
    else:
        _CTX["gate"], _CTX["guard"] = gate, guard


def gate_tamper_rule(segment: str, toks: list[str], words: list[str], cwd: str) -> str | None:
    """push-gate or guard-self when a command that names a protected file could change it."""
    gate, guard = mentions()
    for mention, protected, rule, script_ok in ((gate, is_gate_path, "push-gate", _push_script_ok),
                                                (guard, is_guard_path, "guard-self", _hook_script_ok)):
        if mention.search(segment) and tamper(toks, words, cwd, mention, protected, script_ok):
            return rule
    if "disableAllHooks" in segment and re.search(r"settings(?:\.local)?\.json|hooks\.json|--settings", segment):
        raw = resolve_command(words)[0]
        redirected = any(REDIRECT_OP.fullmatch(t) or REDIRECT_JOINED.match(t) for t in toks)
        if redirected or os.path.basename(raw or "") not in SEARCH_ONLY:
            return "guard-self"
    return None


def tamper(toks, words, cwd, mention, protected, script_ok) -> bool:
    """True when a command that names protected files could change them; reading and copying out are fine."""
    for i, t in enumerate(toks):  # output redirected into a protected file
        target = toks[i + 1] if REDIRECT_OP.fullmatch(t) and i + 1 < len(toks) else None
        m = None if REDIRECT_OP.fullmatch(t) else REDIRECT_JOINED.match(t)
        target = m.group(1) if m else target
        if target and protected(absolute(target, cwd)):
            return True
    raw, args, pre = resolve_command(words)
    if raw is None or any(mention.search(w) for w in pre):
        return True  # a bare assignment or keyword naming it, such as D=~/.git-hooks
    name = os.path.basename(raw)
    if name in GATE_READ_ONLY or name == "launchctl":
        return False
    if name == "plutil":
        return not (("-p" in args or "-lint" in args) and not any(
            a in ("-convert", "-replace", "-insert", "-remove", "-o") for a in args))
    if name in SHELLS:
        return not ("-n" in args and shell_inline(args) is None)
    if name == "sed":
        in_place = any(a.startswith(("-i", "--in-place")) or a == "-I" for a in args)
        return in_place or any(mention.search(a) for a in args[:-1])
    if name == "find":
        writes = {"-delete", "-exec", "-execdir", "-ok", "-okdir"}
        return any(a in writes or a.startswith("-fprint") for a in args)
    if name == "git":
        sub, k, _ = git_subcommand(args)
        return not (sub in READ_GIT or sub == "config" and not config_writes(args[k + 1:]))
    if name in GATE_COPY_OUT:
        operands = [a for a in args if not a.startswith("-")]
        risky = any(a in ("-t", "--target-directory", "--remove-source-files") or
                    a.startswith(("--target-directory=",)) for a in args)
        dest_protected = bool(operands) and protected(absolute(operands[-1], cwd))
        return not (operands and not risky and not dest_protected)
    if name in INTERPRETERS or re.fullmatch(r"python3?\.\d+", name):
        if name.startswith("python"):
            for k, a in enumerate(args[:-1]):
                if a == "-m" and args[k + 1] in ("py_compile", "unittest", "pytest"):
                    return False  # compiling or testing reads the files
                if a == "-c" and not WRITE_HINT.search(args[k + 1]):
                    return False  # inline code that only reads
        if any(a in INLINE_FLAGS or a == "-m" for a in args):
            return True
        script = next((a for a in args if not a.startswith("-")), None)
        if script and script_ok(absolute(script, cwd)):
            return False
    return True


def inline_code_rule(code: str, heredoc: bool = False, cwd: str = "/") -> str | None:
    """The rule for program text an interpreter runs inline (-c, or a heredoc read as the program)."""
    if heredoc:  # a long program: judge the files it opens and the paths it names, not every word
        for m in FILE_READ.finditer(code):
            name = m.group(2) or m.group(4)
            if classify_path(absolute(name, cwd)) == "key-store" or KEY_MENTION.search(" " + name):
                return "key-store"
            if is_env_name(os.path.basename(name)):
                return "env-file"
        if PUSH_CODE.search(code) and EXEC_HINT.search(code):
            return "push-bypass"
        if WRITE_HINT.search(code):
            for m in PATH_LITERAL.finditer(code):
                target = absolute(m.group(2), cwd)
                if is_gate_path(target):
                    return "push-gate"
                if is_guard_path(target):
                    return "guard-self"
        return None
    else:
        if KEY_MENTION.search(code):
            return "key-store"
        if env_mentions(code):
            return "env-file"
    if PUSH_CODE.search(code) and EXEC_HINT.search(code):
        return "push-bypass"
    if WRITE_HINT.search(code):
        gate, guard = mentions()
        if gate.search(code):
            return "push-gate"
        if guard.search(code):
            return "guard-self"
    return None


def reads_stdin(args: list[str]) -> bool:
    """True when a shell or interpreter with these arguments takes its program from standard input."""
    operands = [a for a in args if not a.startswith("-") or a == "-"]
    return not operands or operands[0] in ("-", "/dev/stdin", "/dev/fd/0")


def body_rule(cmd: str, args: list[str], body: str, cwd: str, depth: int) -> str | None:
    """The rule for program text piped or fed to cmd, when cmd runs it."""
    if cmd in SHELLS and depth < 4 and shell_inline(args) is None and reads_stdin(args):
        return decide_shell(body, cwd, depth + 1)
    if (cmd in INTERPRETERS or cmd and re.fullmatch(r"python3?\.\d+", cmd)) and \
            not any(a in INLINE_FLAGS for a in args) and reads_stdin(args):
        return inline_code_rule(body, heredoc=True, cwd=cwd)
    return None


def heredoc_rule(opener: str, body: str, cwd: str, depth: int) -> str | None:
    """Judge a heredoc body that a shell runs or an interpreter reads as its program, directly or through a pipe."""
    segs = segments(opener)
    for k, (segment, sep) in enumerate(segs):
        if "<<" not in segment:
            raw, args, _ = resolve_command(tokens_of(segment))
            if raw and os.path.basename(raw) in ("cd", "pushd") and args:
                cwd = absolute(args[-1], cwd)
            continue
        words, toks, i = [], tokens_of(segment), 0
        while i < len(toks):  # drop the heredoc marker and any redirections
            t = toks[i]
            if t in ("<<", "<<-") or REDIRECT_OP.fullmatch(t) or t in ("<", "0<"):
                i += 2
            elif t.startswith(("<<", "<")) or REDIRECT_JOINED.match(t) or re.fullmatch(r"\d*>&\d*", t):
                i += 1
            else:
                words.append(t)
                i += 1
        cmd, args = command_words(words)
        rule = body_rule(cmd, args, body, cwd, depth)  # bash <<EOF, bash -s, python3 - <<EOF
        if rule:
            return rule
        j = k
        while sep == "|" and j + 1 < len(segs):  # cat <<EOF | bash: the body flows down the pipe
            j += 1
            nxt, sep = segs[j]
            ncmd, nargs = command_words(tokens_of(nxt))
            rule = body_rule(ncmd, nargs, body, cwd, depth)
            if rule:
                return rule
        return None
    return None


AGENT_ENV = re.compile(r"^(?:CLAUDE_CONFIG_DIR|CODEX_HOME|HOME|XDG_CONFIG_HOME)$")


def agent_rule(cmd: str, args: list[str], pre: tuple = ()) -> str | None:
    """guard-self when this starts a coding agent without the user settings that hold its hooks."""
    if cmd not in ("claude", "codex"):
        return None
    wrapper = None
    for w in pre:
        if os.path.basename(w) in WRAPPERS:
            wrapper = os.path.basename(w)
        elif wrapper == "env" and w.startswith("-") and w != "--" or \
                ASSIGN.fullmatch(w) and AGENT_ENV.match(w.split("=", 1)[0]):
            return "guard-self"
    for k, a in enumerate(args):
        flag = a.split("=", 1)[0]
        if flag in AGENT_UNGUARDED:
            return "guard-self"
    return None


def judge_words(words: list[str], reads: list[str], cwd: str, depth: int, to_file: bool = False) -> str | None:
    for r in reads:
        rule = token_rule(r, cwd)
        if rule:
            return rule
    split = env_split(words)
    if split is not None and depth < 4:
        return decide_shell(split, cwd, depth + 1)
    rule = push_bypass_rule(words)
    if rule:
        return rule
    cmd, args = command_words(words)
    if cmd is None:
        return None
    rule = agent_rule(cmd, args, tuple(resolve_command(words)[2]))
    if rule:
        return rule
    if cmd == "eval" and depth < 4:
        return decide_shell(" ".join(args), cwd, depth + 1)
    if cmd == "find" and depth < 4:
        for k, a in enumerate(args):
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                end = next((m for m in range(k + 1, len(args)) if args[m] in (";", "\\;", "+")), len(args))
                rule = judge_words(args[k + 1:end], [], cwd, depth + 1)
                if rule:
                    return rule
    if cmd == "git" and depth < 4:
        sub, k, _ = git_subcommand(args)
        rest = args[k + 1:]
        inner = None
        if sub == "submodule" and "foreach" in rest:
            inner = " ".join(a for a in rest[rest.index("foreach") + 1:] if a != "--recursive")
        elif sub == "rebase":
            for m, a in enumerate(rest):
                if a in ("-x", "--exec") and m + 1 < len(rest):
                    inner = rest[m + 1]
                elif a.startswith("--exec="):
                    inner = a.split("=", 1)[1]
        if inner:
            rule = decide_shell(inner, cwd, depth + 1)
            if rule:
                return rule
    if cmd == "docker" and args[:1] == ["exec"] or cmd == "kubectl" and args[:1] == ["exec"]:
        rest = args[1:]
        if "--" in rest:
            rest = rest[rest.index("--") + 1:]
        else:
            k = 0
            while k < len(rest) and rest[k].startswith("-"):
                k += 2 if rest[k] in ("-e", "--env", "-u", "--user", "-w", "--workdir", "-c", "--container",
                                      "-n", "--namespace") else 1
            rest = rest[k + 1:]
        return judge_words(rest, [], cwd, depth + 1) if depth < 4 else None
    if cmd in DUMP_ALONE and (not args or (cmd in ("export", "declare", "typeset") and
                                           all(a.startswith("-") for a in args))):
        return "env-dump"
    if cmd == "printenv":
        if to_file:
            return None  # written to a file, not shown
        return "secret-env-var" if any(is_secret_var(a) for a in args if not a.startswith("-")) else None
    if cmd in ("echo", "printf", "print"):
        if to_file:
            return None
        return "secret-env-var" if any(is_secret_var(v) for a in args for v in VAR_REF.findall(a)) else None
    for k, a in enumerate(args):  # here-strings: bash <<< "cmd", python3 <<< "code"
        text = args[k + 1] if a == "<<<" and k + 1 < len(args) else a[3:] if a.startswith("<<<") else None
        if text is not None:
            rule = body_rule(cmd, [x for x in args if not x.startswith("<<<") and x != text], text, cwd, depth)
            if rule:
                return rule
    if cmd in SHELLS:
        inline = shell_inline(args)
        return decide_shell(inline, cwd, depth + 1) if inline is not None and depth < 4 else None
    if cmd in INTERPRETERS or re.fullmatch(r"python3?\.\d+", cmd):
        for k, a in enumerate(args[:-1]):
            if a in INLINE_FLAGS:
                rule = inline_code_rule(args[k + 1])
                if rule:
                    return rule
        return None
    rules = {token_rule(a, cwd) for a in args}
    if cmd == "git" and args[:1] in (["add"], ["commit"], ["stash"]) and rules & {"env-file", "key-store"}:
        return "secret-into-git"
    if cmd in DISPLAY and "key-store" in rules or cmd in COPY and "key-store" in rules:
        return "key-store"
    if cmd in DISPLAY and "env-file" in rules:
        if cmd in ("grep", "egrep", "fgrep", "zgrep", "rg") and quiet_search(args):
            return None  # prints counts, file names or nothing, never a line
        return "env-file"
    return None


def quiet_search(args: list[str]) -> bool:
    long_ok = {"--count", "--quiet", "--silent", "--files-with-matches", "--files-without-match"}
    return any(a in long_ok or (a.startswith("-") and not a.startswith("--") and set(a[1:]) & set("cqlL"))
               for a in args)


def judge_segment(segment: str, cwd: str, depth: int) -> str | None:
    toks = tokens_of(segment)
    words, reads, i, to_file = [], [], 0, False
    while i < len(toks):  # drop output-redirect targets, keep input-redirect sources
        t = toks[i]
        if re.fullmatch(r"\d*>>?|&>>?|>\||\d*>&\d*", t):
            if not t.startswith("2") and "&" not in t[1:] and t != ">&2":
                to_file = to_file or not (i + 1 < len(toks) and toks[i + 1] in ("/dev/stdout", "/dev/tty"))
            i += 2 if not re.fullmatch(r"\d*>&\d+", t) else 1
            continue
        if re.match(r"\d*>>?\S", t) or re.match(r"&>>?\S", t):
            if not t.startswith("2") and not re.match(r"\d*>&\d", t):
                to_file = to_file or not t.endswith(("/dev/stdout", "/dev/tty"))
            i += 1
            continue
        if t in ("<", "0<"):
            if i + 1 < len(toks):
                reads.append(toks[i + 1])
            i += 2
            continue
        if t.startswith("<") and not t.startswith("<<"):
            reads.append(t[1:])
            i += 1
            continue
        words.append(t)
        i += 1
    rule = gate_tamper_rule(segment, toks, words, cwd)
    if rule:
        return rule
    return judge_words(words, reads, cwd, depth, to_file)


def filtered_by_safe_grep(segment: str) -> bool:
    """True when this pipe stage keeps only lines matching a pattern with no secret word in it."""
    cmd, args = command_words(tokens_of(segment))
    if cmd not in ("grep", "egrep", "rg"):
        return False
    if any(a.startswith("-") and "v" in a.lstrip("-") and not a.startswith("--") for a in args) or "--invert-match" in args:
        return False
    pats = [a for a in args if not a.startswith("-")]
    return bool(pats) and not any(is_secret_var(w) for p in pats[:1] for w in re.findall(r"[A-Za-z_]+", p))


def decide_shell(command: str, cwd: str = "", depth: int = 0) -> str | None:
    text, bodies = split_heredocs(command)
    set_context(text, cwd, inherit=depth > 0)
    if KEYCHAIN.search(text):
        return "keychain-secret"
    for opener, body in bodies:
        rule = heredoc_rule(opener, body, cwd_before(text, opener, cwd), depth)
        if rule:
            return rule
    segs = segments(text)
    rule = push_env_rule(segs)
    if rule:
        return rule
    here = cwd
    for k, (segment, sep) in enumerate(segs):
        if not segment.strip():
            continue
        rule = judge_segment(segment, here, depth)
        raw, args, _ = resolve_command(tokens_of(segment))
        if raw and os.path.basename(raw) in ("cd", "pushd") and args:
            here = absolute(args[-1], here)  # later segments resolve paths from the new folder
        if rule == "env-dump" and sep == "|" and k + 1 < len(segs) and filtered_by_safe_grep(segs[k + 1][0]):
            continue
        if rule:
            return rule
    return None


def cwd_before(text: str, opener: str, cwd: str) -> str:
    """The folder a command is in when it reaches the line that opens a heredoc, following cd."""
    here = cwd
    for line in text.split("\n"):
        if line == opener:
            break
        for segment, _ in segments(line):
            raw, args, _ = resolve_command(tokens_of(segment))
            if raw and os.path.basename(raw) in ("cd", "pushd") and args:
                here = absolute(args[-1], here)
    return here


def push_env_rule(segs) -> str | None:
    """A rule when one segment exports or unsets a variable that a push or an agent depends on and
    another segment pushes or starts that agent."""
    push_env = agent_env = pushes = agents = False
    for segment, _ in segs:
        words = tokens_of(segment)
        raw, args, pre = resolve_command(words)
        name = os.path.basename(raw) if raw else None
        names = []
        if name in ("export", "unset", "declare", "typeset"):
            names = [a.split("=", 1)[0] for a in args if not a.startswith("-")]
        elif raw is None:
            names = [w.split("=", 1)[0] for w in pre if ASSIGN.fullmatch(w)]
        push_env = push_env or any(PUSH_ENV.match(n) for n in names)
        agent_env = agent_env or any(AGENT_ENV.match(n) for n in names)
        pushes = pushes or name == "git" and git_subcommand(args)[0] in PUSH_SUBS
        agents = agents or name in ("claude", "codex")
    if push_env and pushes:
        return "push-bypass"
    if agent_env and agents:
        return "guard-self"
    return None


def resulting_text(tool: str, ti: dict, current: str) -> str | None:
    """The file text after this Write, Edit or MultiEdit, or None when it cannot be worked out."""
    if tool == "Write":
        content = ti.get("content")
        return content if isinstance(content, str) else None
    edits = [ti] if tool == "Edit" else ti.get("edits") if tool == "MultiEdit" else None
    if not isinstance(edits, list):
        return None
    text = current
    for e in edits:
        if not isinstance(e, dict):
            return None
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if old == "":
            text = new if text == "" else None
            if text is None:
                return None
        elif e.get("replace_all"):
            text = text.replace(old, new)
        else:
            text = text.replace(old, new, 1)
    return text


def safety_entries(settings) -> set:
    """The hook wiring and deny rules that must survive any edit of a settings file."""
    out = set()
    if not isinstance(settings, dict):
        return out
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    for event, groups in hooks.items():
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, dict):
                continue
            for h in g.get("hooks") or []:
                cmd = h.get("command") if isinstance(h, dict) else None
                if isinstance(cmd, str) and any(s in cmd for s in SAFETY_HOOKS):
                    out.add(("hook", event, str(g.get("matcher", "")), cmd))
    perms = settings.get("permissions") if isinstance(settings.get("permissions"), dict) else {}
    for rule in perms.get("deny") or []:
        out.add(("deny", str(rule)))
    return out


def settings_rule(tool: str, ti: dict, path: str) -> str | None:
    """guard-self when an edit of a settings file would switch a safety hook or deny rule off."""
    project = os.path.basename(path) in ("settings.json", "settings.local.json") and \
        os.path.basename(os.path.dirname(path)) == ".claude"
    user = is_settings_file(path)
    if not (project or user):
        return None
    try:
        with open(path) as fh:
            current = fh.read()
    except FileNotFoundError:
        current = ""
    except OSError:
        return "guard-self"
    new = resulting_text(tool, ti, current)
    if new is None or re.search(r'"disableAllHooks"\s*:\s*true', new):
        return "guard-self"
    try:
        after = json.loads(new) if new.strip() else {}
    except ValueError:
        return "guard-self" if user else None  # a project file Claude Code cannot parse switches nothing off
    if isinstance(after, dict) and after.get("disableAllHooks"):
        return "guard-self"
    if user:
        try:
            before = json.loads(current) if current.strip() else {}
        except ValueError:
            before = {}
        if not safety_entries(before) <= safety_entries(after):
            return "guard-self"
    return None


def decide(tool: str, tool_input: dict, cwd: str):
    """(denied, rule) for one tool call."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool == "Bash":
        rule = decide_shell(str(ti.get("command") or ""), cwd)
        return (rule is not None), rule
    if tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        raw = ti.get("file_path") or ti.get("notebook_path") or ti.get("path")
        if not raw:
            return False, None
        p = absolute(raw, cwd)
        rule = classify_path(p)
        if rule is None and tool != "Read" and is_gate_path(p):
            return True, "push-gate"
        if rule is None and tool != "Read" and is_guard_file(p):
            return True, "guard-self"
        if rule is None and tool != "Read":
            rule = settings_rule(tool, ti, p)
            if rule:
                return True, rule
        if rule == "env-file" and tool == "Write":
            return (True, "env-overwrite") if os.path.lexists(p) else (False, None)
        return (rule is not None), rule
    if tool in ("Grep", "Glob"):
        target = absolute(ti.get("path") or cwd, cwd)
        rule = classify_path(target)
        if not rule and tool == "Grep" and os.path.realpath(target) == os.path.join(H, ".ssh"):
            rule = "key-store"  # searching the folder reads the private keys in it; listing names does not
        if not rule and tool == "Grep" and env_mentions(" " + str(ti.get("glob") or "")):
            rule = "env-file"
        return (rule is not None), rule
    return False, None


def log(entry: dict) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def main() -> None:
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "claude"
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        sys.exit(0)
    if not isinstance(payload, dict):
        sys.exit(0)
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if isinstance(ti.get("command"), list):
        ti = dict(ti, command=shlex.join(str(x) for x in ti["command"]))
    cwd = str(payload.get("cwd") or os.getcwd())
    try:
        denied, rule = decide(tool, ti, cwd)
    except Exception:
        raw = json.dumps(ti)
        denied = tool in GUARDED and bool(KEY_MENTION.search(raw) or env_mentions(raw) or GATE_MENTION.search(raw)
                                          or GUARD_MENTION.search(raw))
        rule = "guard-error" if denied else None
    if not denied:
        sys.exit(0)
    log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": mode, "tool": tool, "rule": rule})
    if rule in PUSH_RULES:
        verb = "skip" if rule == "push-bypass" else "change"
        reason = (f"Blocked by the push gate guard: this call would {verb} {RULES[rule]}. Every push and merge "
                  "needs the owner's approval, and agents never change the gate itself. Push with a "
                  "plain `git push`. To merge a pull request, run `/usr/bin/python3 -I ~/.git-hooks/push_approval.py "
                  "merge --repo OWNER/REPO --pr N --squash` (or --merge or --rebase, optionally --delete-branch); it "
                  "asks the owner and merges only after their approval. If the gate needs a change, ask the owner.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
        print(reason, file=sys.stderr)
        sys.exit(2)
    if rule == "guard-self":
        reason = (f"Blocked by the guard: this call would change {RULES[rule]}. Agents can read and run the "
                  "safety hooks but never change them. Put a new version in ~/.claude/hooks-staging/ (same "
                  "path as under ~/.claude/hooks/) and ask the owner to install it after reviewing the diff"
                  ". settings.json "
                  "can be edited with the Edit tool as long as every safety hook and deny rule stays.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
        print(reason, file=sys.stderr)
        sys.exit(2)
    reason = (f"Blocked by the secret guard: this call would read, print or copy {RULES[rule]}. Secrets never "
              "enter the conversation or leave their file. To give a program its .env, pass the file to the "
              "program (for example `--env-file .env`, or `source .env && <command>`) instead of printing it. "
              "Templates such as .env.example are allowed. If a secret value is really needed, ask the owner.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    print(reason, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
