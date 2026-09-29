"""Install the harness into an agent's own configuration, and take it out again.

Every install writes a manifest under <harness home>/installs/<host>.json listing each file it touched, whether the
file existed, and a byte-for-byte backup. Uninstall removes only what the harness added (hook entries whose command
names this harness, the marked block in a rules file, the skill folders it copied) and, when nothing else changed
in a file since the install, restores the backup bytes exactly. `--dry-run` prints the diff and writes nothing.
"""
import difflib, json, os, re, shutil, sys, time

from .policy import home

MARK = "nodaris-harness"
BEGIN, END = "<!-- nodaris-harness:begin -->", "<!-- nodaris-harness:end -->"
ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Host: config file, hook shape, rules file, skills folder, and the degree of enforcement we can claim.
CLAUDE_EVENTS = [("PreToolUse", "Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob|Task|Agent"), ("UserPromptSubmit", None),
                 ("PostToolUse", "Bash|Write|Edit|MultiEdit|NotebookEdit|Task|Agent|Skill"),
                 ("PostToolUseFailure", "Bash|Write|Edit|MultiEdit|NotebookEdit"), ("Stop", None), ("SubagentStop", None),
                 ("PreCompact", None),
                 ("SessionStart", None)]
CODEX_EVENTS = [("PreToolUse", None), ("UserPromptSubmit", None), ("PostToolUse", None), ("Stop", None),
                ("PreCompact", None), ("SessionStart", None)]
GEMINI_EVENTS = [("BeforeTool", "run_shell_command|read_file|read_many_files|write_file|replace"), ("BeforeAgent", None),
                 ("AfterTool", "run_shell_command|write_file|replace"), ("AfterAgent", None), ("PreCompress", None),
                 ("SessionStart", None)]
CURSOR_EVENTS = ["beforeShellExecution", "beforeReadFile", "afterFileEdit", "afterShellExecution", "postToolUseFailure",
                 "beforeSubmitPrompt", "stop", "preCompact", "sessionStart"]
LEVEL = {"claude": "live-verified by `doctor --live`", "codex": "contract-tested", "gemini": "contract-tested",
         "cursor": "contract-tested (closed source; from the published docs)",
         "opencode": "contract-tested; no prompt, stop or session hooks exist in OpenCode, so the done gate, prompt "
                     "redaction and intake are rules-file only there", "git": "git hooks only (advisory for the agent)"}


def python():
    return sys.executable or "python3"


def bin_path(root=None):
    return os.path.join(root or ENGINE_ROOT, "bin", "nodaris-harness")


COMMANDS = ("nodaris", "nodaris-harness")


def link_commands(home=None, dry_run=False):
    """Put nodaris and nodaris-harness on the PATH as links in ~/.local/bin. Returns the links made. A file the
    person already has under either name is left alone."""
    local = os.path.join(home or os.path.expanduser("~"), ".local", "bin")
    target, made = bin_path(), []
    for name in COMMANDS:
        link = os.path.join(local, name)
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(target):
            continue
        stale = (os.path.islink(link) and not os.path.exists(link)
                 and os.readlink(link).endswith(os.path.join("bin", "nodaris-harness")))   # a moved harness checkout
        if os.path.lexists(link) and not stale:
            continue
        if not dry_run:
            os.makedirs(local, exist_ok=True)
            if stale:
                os.unlink(link)
            os.symlink(target, link)
        made.append(link)
    return made


def unlink_commands(home=None):
    local = os.path.join(home or os.path.expanduser("~"), ".local", "bin")
    for name in COMMANDS:
        link = os.path.join(local, name)
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(bin_path()):
            os.unlink(link)


def hook_cmd(host, event=None, root=None):
    cmd = f'"{python()}" "{bin_path(root)}" hook --host {host}'
    return cmd + (f" --event {event}" if event else "")


def paths(host, user_home=None, config_dir=None, project=None):
    h = user_home or os.path.expanduser("~")
    if host == "claude":
        base = config_dir or os.path.join(h, ".claude")
        return {"config": os.path.join(base, "settings.json"), "rules": os.path.join(base, "CLAUDE.md"),
                "skills": os.path.join(base, "skills"), "agents": os.path.join(base, "agents")}
    if host == "codex":
        base = config_dir or os.path.join(h, ".codex")
        return {"config": os.path.join(base, "hooks.json"), "rules": os.path.join(base, "AGENTS.md"),
                "skills": os.path.join(h, ".agents", "skills")}
    if host == "gemini":
        base = config_dir or os.path.join(h, ".gemini")
        return {"config": os.path.join(base, "settings.json"), "rules": os.path.join(base, "GEMINI.md"),
                "skills": os.path.join(base, "skills")}
    if host == "cursor":
        base = config_dir or os.path.join(h, ".cursor")
        out = {"config": os.path.join(base, "hooks.json")}
        if project:
            out["rules"] = os.path.join(project, ".cursor", "rules", "nodaris-harness.mdc")
        return out
    if host == "opencode":
        base = config_dir or os.path.join(h, ".config", "opencode")
        return {"plugin": os.path.join(base, "plugin", "nodaris-harness.js"), "rules": os.path.join(base, "AGENTS.md"),
                "skills": os.path.join(base, "skills")}
    if host == "git":
        if not project:
            raise SystemExit("git hooks are installed per repository: pass --project DIR")
        return {"pre-commit": os.path.join(project, ".git", "hooks", "pre-commit"),
                "pre-push": os.path.join(project, ".git", "hooks", "pre-push")}
    raise SystemExit(f"unknown host {host}")


def _read(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def _load_json(raw):
    if not raw or not raw.strip():
        return {}
    return json.loads(raw)


def _ours(entry):
    return MARK in json.dumps(entry)


def _hooks_json(host, raw, root):
    data = _load_json(raw)
    hooks = data.setdefault("hooks", {})
    if host == "cursor":
        data.setdefault("version", 1)
        for ev in CURSOR_EVENTS:
            lst = [e for e in hooks.get(ev, []) if not _ours(e)]
            lst.append({"command": hook_cmd("cursor", ev, root), "timeout": 60})
            hooks[ev] = lst
        return data
    table = {"claude": CLAUDE_EVENTS, "codex": CODEX_EVENTS, "gemini": GEMINI_EVENTS}[host]
    for ev, matcher in table:
        lst = [g for g in hooks.get(ev, []) if not _ours(g)]
        handler = {"type": "command", "command": hook_cmd(host, None, root)}
        if host == "gemini":
            handler.update({"name": "nodaris-harness", "timeout": 60000})
        else:
            handler["timeout"] = 60
        group = {"hooks": [handler]}
        if matcher:
            group["matcher"] = matcher
        lst.append(group)
        hooks[ev] = lst
    if host == "claude" and _harness_setting("deny_rules", True):
        # Secret stores and credential files the agent may never read or edit. Added next to the person's own rules;
        # uninstall removes only the ones that were not there before.
        perms = data.setdefault("permissions", {})
        deny = list(perms.get("deny") or [])
        deny += [r for r in deny_rules(root) if r not in deny]
        perms["deny"] = deny
    if host == "claude" and _harness_setting("statusline", True) and not data.get("statusLine"):
        # Only when the person has no status line of their own; uninstall removes it again.
        data["statusLine"] = {"type": "command", "command": f'"{python()}" "{bin_path(root)}" statusline', "padding": 0}
    if host == "gemini":
        ctx = data.setdefault("context", {})
        names = ctx.get("fileName") or ["GEMINI.md"]
        names = [names] if isinstance(names, str) else list(names)
        if "GEMINI.md" not in names:
            names.append("GEMINI.md")
        ctx["fileName"] = names
    return data


def deny_rules(root=None):
    try:
        with open(os.path.join(root or ENGINE_ROOT, "packs", "core", "vendor", "deny.json")) as fh:
            rules = json.load(fh)
        return [r for r in rules if isinstance(r, str)]
    except (OSError, ValueError):
        return []


def _harness_setting(key, default):
    try:
        with open(os.path.join(home(), "settings.json")) as fh:
            data = json.load(fh)
        return data.get(key, default) if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def _strip_hooks(raw, before=None, root=None):
    data = _load_json(raw)
    perms = data.get("permissions")
    if isinstance(perms, dict) and isinstance(perms.get("deny"), list):
        prior = (before or {}).get("permissions")
        prior = prior if isinstance(prior, dict) else None
        had = set((prior or {}).get("deny") or [])
        ours = set(deny_rules(root))
        perms["deny"] = [r for r in perms["deny"] if r not in ours or r in had]
        if not perms["deny"] and "deny" not in (prior or {}):
            del perms["deny"]
        if not perms and prior is None:
            del data["permissions"]
    if "nodaris-harness\" statusline" in json.dumps(data.get("statusLine") or {}).replace("\\\"", "\""):
        del data["statusLine"]
    hooks = data.get("hooks", {})
    for ev in list(hooks):
        hooks[ev] = [g for g in hooks[ev] if not _ours(g)]
        if not hooks[ev]:
            del hooks[ev]
    if not hooks and "hooks" in data:
        del data["hooks"]
    return data


def _with_block(raw, body):
    text = (raw or b"").decode()
    if BEGIN in text and END in text:
        text = text[:text.index(BEGIN)] + text[text.index(END) + len(END):]
        text = text.rstrip("\n") + ("\n" if text.strip() else "")
    block = f"{BEGIN}\n{body.strip()}\n{END}\n"
    return (text + ("\n" if text.strip() else "") + block).encode()


def _without_block(raw):
    text = (raw or b"").decode()
    if BEGIN not in text:
        return text.encode()
    before, after = text[:text.index(BEGIN)], text[text.index(END) + len(END):]
    return (before.rstrip("\n") + ("\n" if before.strip() else "") + after.lstrip("\n")).encode()


def rules_text(root=None, packs=None):
    """The rules file for this install. Text marked for a pack (<!-- pack:NAME -->...<!-- /pack -->) is kept only when
    that pack is installed, so a general install never names skills it does not have."""
    with open(os.path.join(root or ENGINE_ROOT, "rules", "RULES.md")) as fh:
        text = fh.read()
    if packs is None:
        packs = _harness_setting("packs", None) or list(PACKS)
    def keep(m):
        return (m.group(1) or "") + m.group(3) if m.group(2) in packs else ""
    return re.sub(r"(\n[ \t]*)?<!-- pack:([a-z]+) -->(.*?)<!-- /pack -->", keep, text, flags=re.S)


def opencode_plugin(root=None):
    return f"""// nodaris-harness: sends each tool call to the harness engine and enforces its answer.
import {{ spawnSync }} from "node:child_process";

const PY = {json.dumps(python())};
const BIN = {json.dumps(bin_path(root))};

function ask(event, body) {{
  const r = spawnSync(PY, [BIN, "hook", "--host", "opencode", "--event", event],
    {{ input: JSON.stringify(body), encoding: "utf8", timeout: 60000 }});
  if (r.error || r.status !== 0) return {{ decision: "deny", reason: "The harness could not check this call, so it did not run." }};
  try {{ return JSON.parse(r.stdout || "{{}}"); }}
  catch {{ return {{ decision: "deny", reason: "The harness returned an unreadable answer, so the call did not run." }}; }}
}}

export const NodarisHarness = async ({{ directory }}) => ({{
  "tool.execute.before": async (input, output) => {{
    const res = ask("tool.execute.before", {{ tool: input.tool, sessionID: input.sessionID, callID: input.callID,
      args: output.args, cwd: directory }});
    if (res.decision === "deny") throw new Error(res.reason);
  }},
  "tool.execute.after": async (input, output) => {{
    try {{
      const res = ask("tool.execute.after", {{ tool: input.tool, sessionID: input.sessionID, callID: input.callID,
        args: input.args, output: output.output, cwd: directory }});
      if (res.context) output.output = `${{output.output ?? ""}}\\n\\n${{res.context}}`;
    }} catch {{}}
  }},
}});
"""


def git_hook(stage, root=None):
    return f"""#!/bin/sh
# nodaris-harness {stage} hook: secrets and PHI scan on what is being committed, protected-branch and AI-mark checks on push.
exec "{python()}" "{bin_path(root)}" gitcheck --stage {stage} "$@"
"""


def plan(host, user_home=None, config_dir=None, project=None, root=None):
    """Return [(path, old bytes or None, new bytes, kind)] for an install. Pure: reads files, writes nothing."""
    p = paths(host, user_home, config_dir, project)
    out = []
    if "config" in p:
        raw = _read(p["config"])
        new = (json.dumps(_hooks_json(host, raw, root), indent=2) + "\n").encode()
        out.append((p["config"], raw, new, "json-hooks"))
    if "plugin" in p:
        out.append((p["plugin"], _read(p["plugin"]), opencode_plugin(root).encode(), "file"))
    if host == "git":
        for stage in ("pre-commit", "pre-push"):
            out.append((p[stage], _read(p[stage]), git_hook(stage, root).encode(), "file"))
    if "rules" in p:
        body = rules_text(root)
        if host == "cursor":
            out.append((p["rules"], _read(p["rules"]),
                        ("---\ndescription: Engineering harness rules\nalwaysApply: true\n---\n" + body).encode(), "file"))
        else:
            out.append((p["rules"], _read(p["rules"]), _with_block(_read(p["rules"]), body), "text-block"))
    return out, p


PACKS = ("core", "healthcare", "creative")


def selected_packs():
    """Packs chosen at onboarding (settings.json in the harness home); every pack when nothing was chosen."""
    try:
        chosen = json.load(open(os.path.join(home(), "settings.json"))).get("packs")
    except (OSError, ValueError):
        chosen = None
    chosen = [p for p in (chosen or PACKS) if p in PACKS]
    return ["core"] + [p for p in chosen if p != "core"]


def skill_sources(root=None, packs=None):
    base = root or ENGINE_ROOT
    dirs = [os.path.join(base, "skills")] + [os.path.join(base, "packs", p, "skills") for p in (packs or selected_packs())]
    dirs += [os.path.join(base, "pack", "skills"), os.path.join(base, "pack", "vendor", "skills")]
    out = []
    for d in dirs:
        if os.path.isdir(d):
            out += [os.path.join(d, n) for n in sorted(os.listdir(d)) if os.path.exists(os.path.join(d, n, "SKILL.md"))]
    return out


def diff(changes):
    lines = []
    for path, old, new, _ in changes:
        a = (old or b"").decode(errors="replace").splitlines(keepends=True)
        b = new.decode(errors="replace").splitlines(keepends=True)
        lines += difflib.unified_diff(a, b, fromfile=path + (" (absent)" if old is None else ""), tofile=path)
    return "".join(lines)


def manifest_path(host):
    d = os.path.join(home(), "installs")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, host + ".json")


def install(host, user_home=None, config_dir=None, project=None, root=None, dry_run=False, skills=True):
    changes, p = plan(host, user_home, config_dir, project, root)
    report = {"host": host, "level": LEVEL[host], "diff": diff(changes), "files": [], "skills": []}
    if dry_run:
        return report
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backups = os.path.join(home(), "backups", f"{host}-{stamp}")
    previous = json.load(open(manifest_path(host))) if os.path.exists(manifest_path(host)) else None
    entries = []
    for path, old, new, kind in changes:
        prior = next((f for f in (previous or {}).get("files", []) if f["path"] == path), None)
        backup = prior["backup"] if prior else None
        existed = prior["existed"] if prior else old is not None
        if old is not None and not prior:
            os.makedirs(backups, mode=0o700, exist_ok=True)
            backup = os.path.join(backups, str(len(entries)) + "-" + os.path.basename(path))
            with open(backup, "wb") as fh:
                fh.write(old)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(new)
        if kind == "file" and host == "git":
            os.chmod(path, 0o755)
        entries.append({"path": path, "existed": existed, "backup": backup, "kind": kind, "installed_sha": _sha(new)})
    copied = list((previous or {}).get("skills", []))
    if skills and "skills" in p:
        for src in skill_sources(root):
            dst = os.path.join(p["skills"], os.path.basename(src))
            if os.path.exists(dst) and dst not in copied:
                report.setdefault("skipped", []).append(f"{dst} exists and is not the harness's; left alone")
                continue
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
            if dst not in copied:
                copied.append(dst)
    manifest = {"host": host, "installed": stamp, "root": root or ENGINE_ROOT, "files": entries, "skills": copied}
    with open(manifest_path(host), "w") as fh:
        json.dump(manifest, fh, indent=1)
    report["files"], report["skills"] = [e["path"] for e in entries], copied
    return report


def _sha(b):
    import hashlib
    return hashlib.sha256(b).hexdigest()


def uninstall(host):
    mp = manifest_path(host)
    if not os.path.exists(mp):
        return {"host": host, "removed": [], "note": "nothing installed"}
    m = json.load(open(mp))
    removed, kept = [], []
    for f in m["files"]:
        path, cur = f["path"], _read(f["path"])
        if cur is None:
            continue
        original = _read(f["backup"]) if f.get("backup") else None
        if f["kind"] == "json-hooks":
            before = _load_json(original) if original is not None else {}
            stripped = _strip_hooks(cur, before, m.get("root"))
            if host == "gemini":
                had = (before.get("context") or {}).get("fileName")
                had = [had] if isinstance(had, str) else (had or [])
                ctx = stripped.get("context") or {}
                names = ctx.get("fileName")
                if isinstance(names, list) and "GEMINI.md" in names and "GEMINI.md" not in had:
                    names.remove("GEMINI.md")
                    if not names:
                        del ctx["fileName"]
                    if not ctx:
                        stripped.pop("context", None)
            if original is not None and stripped == before:
                new = original
            else:
                new = (json.dumps(stripped, indent=2) + "\n").encode() if stripped else None
        elif f["kind"] == "text-block":
            new = _without_block(cur)
            if original is not None and new.strip() == original.strip():
                new = original
            if not new.strip() and not f["existed"]:
                new = None
        else:
            new = original
            if _sha(cur) != f.get("installed_sha"):
                if original is None:
                    kept.append(f"{path} was changed after install; left in place")
                    continue
                # The person edited the harness's copy; restore theirs and keep the edited one beside it.
                aside = path + ".edited-after-install"
                with open(aside, "wb") as fh:
                    fh.write(cur)
                kept.append(f"{path} was changed after install; the original is restored and the edited "
                            f"version is saved as {aside}")
        if new is None or (not f["existed"] and not new.strip()):
            os.remove(path)
        else:
            with open(path, "wb") as fh:
                fh.write(new)
        removed.append(path)
    for d in m.get("skills", []):
        if os.path.isdir(d):
            shutil.rmtree(d)
            removed.append(d)
    os.remove(mp)
    return {"host": host, "removed": removed, "kept": kept}
