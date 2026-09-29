"""Self-contained team build of the harness: everything a teammate needs, nothing from the owner's machine.

  python3 team.py vendor                 # owner's machine only: refresh packs/core/vendor/ and the owner's skills
  python3 team.py build DEST [PACKS]     # any machine: assemble a CLAUDE_CONFIG_DIR at DEST from this folder alone;
                                         # PACKS is a comma list of core, healthcare, creative (default: all three)
  DEST/launch.sh [claude args]   # start Claude Code with the harness

`vendor` copies the two safety guards (secrets, destructive commands), five general engineering skills and the
generic deny rules into packs/core/vendor/ and the owner's skills into their pack's skills folder. Personal wording (names, the owner's approval channel, paths to scripts that
exist only on the owner's machine) is replaced by exact substitutions; a substitution that no longer matches stops
the vendor step so drift is noticed. Each vendored guard is then run side by side with the installed original on a
fixed set of tool calls and must reach the same decision on every one.

`build` reads only this folder. Hook commands and deny rules carry DEST's absolute path, so rebuild after moving it.
"""
import hashlib, json, os, pathlib, shutil, stat, subprocess, sys, tempfile, time

HERE = pathlib.Path(__file__).resolve().parent
PACKS = HERE / "packs"
PACK = PACKS / "core"
VENDOR = PACK / "vendor"
PACK_NAMES = ("core", "healthcare", "creative")
HOME = pathlib.Path.home()
NH_RULE = ("/" + os.environ["NODARIS_HARNESS_HOME"]) if os.environ.get("NODARIS_HARNESS_HOME") else "~/.nodaris-harness"
ENGINE = HERE / "engine" / "nodaris_harness"

GUARDS = {
    "secret-guard.py": [
        ('LOG_PATH = os.environ.get("SECRET_GUARD_LOG") or os.path.join(H, ".claude", "logs", "secret-guard.jsonl")',
         'LOG_PATH = os.environ.get("SECRET_GUARD_LOG") or os.path.join(os.environ.get("NODARIS_HARNESS_HOME") or os.path.join(os.path.expanduser("~"), ".nodaris-harness"), "logs", "secret-guard.jsonl")'),
        ("needs Varun's signed approval from Telegram", "needs the owner's approval"),
        ("asks Varun and merges only after his signed approval. If the gate needs a change, ask Varun.",
         "asks the owner and merges only after their approval. If the gate needs a change, ask the owner."),
        ("ask Varun to install it with `bash ", "ask the owner to install it after reviewing the diff"),
        ("~/ai-os/scripts/admin/install-guard-update.sh`, which shows him the diff first.", "."),
        ("If a secret value is really needed, ask Varun.", "If a secret value is really needed, ask the owner."),
        ("version goes to ~/.claude/hooks-staging/ and Varun installs it.", "version goes to ~/.claude/hooks-staging/ and the owner installs it."),
    ],
    "destructive-guard.py": [
        ('LOG_PATH = Path.home() / ".claude" / "hooks" / "destructive-guard.log"',
         'LOG_PATH = Path(os.environ.get("DESTRUCTIVE_GUARD_LOG") or Path(os.environ.get("NODARIS_HARNESS_HOME") or Path.home() / ".nodaris-harness") / "logs" / "destructive-guard.log")'),
        ("Practical upshot to flag back to Varun:", "Practical upshot to flag back to the owner:"),
        ("# Varun, 2026-09-24:", "# Owner decision, 2026-09-24:"),
        (" prompts he", " prompts they"),
    ],
}
SKILLS = {
    "secure-build": [
        ("`security-review`, plus `omni.sh compare --panel subscription` on the diff for a second lineage.",
         "`security-review` or the harness `security-reviewer` agent, and a second reviewer from a different model family where the project allows it."),
        ("6. Varun approves", "6. The project owner approves"),
        ("goes only to Claude and to the `subscription` panel (OpenAI and Google through Varun's own subscriptions). Never send it to paid "
         "OpenRouter seats (DeepSeek, Moonshot, Qwen, GLM, MiniMax) or to free seats, whose data terms are not confirmed.",
         "goes only to model providers the project has a signed data agreement with. Never send it to free or pay-as-you-go "
         "model endpoints whose data terms are not confirmed."),
        ("`scripts/security-baseline.sh <repo>`", "`python3 <harness>/tools/scan.py` on the changed files, then the full scanners"),
    ],
    "pocock-tdd": [], "pocock-triage": [], "pocock-grill-me": [], "build-agent-reliability": [],
    # The owner's motion and 3D skills, so the media route has somewhere to send video and animation work.
    "threejs-webgl-scenes": [], "gsap-scroll-motion": [], "motion-design-direction": [], "motion-web-pipeline": [],
    "web-motion-primitives": [],
}
SKILL_PACK = {"threejs-webgl-scenes": "creative", "gsap-scroll-motion": "creative", "motion-design-direction": "creative",
              "motion-web-pipeline": "creative", "web-motion-primitives": "creative"}
PERSONAL_DENY = ("prompt-shaper", "aios-push-approval", "git-approvals", ".git-hooks", ".local/bin/git", "ai-os/",
                 ".claude/hooks", ".claude/profiles", "api-keys.json")
# Tool calls the vendored guards must decide exactly as the installed originals do.
PROBES = [
    ("Bash", {"command": "cat ~/.ssh/id_rsa"}), ("Bash", {"command": "ls -la"}), ("Bash", {"command": "cat .env"}),
    ("Bash", {"command": "grep -r API_KEY .env.production"}), ("Bash", {"command": "git status"}),
    ("Bash", {"command": "git push --no-verify origin dev"}), ("Bash", {"command": "rm -rf ~"}), ("Bash", {"command": "rm -rf /"}),
    ("Bash", {"command": "echo hello > out.txt"}), ("Bash", {"command": "python3 -m pytest -q"}),
    ("Bash", {"command": "printenv | grep SECRET"}), ("Bash", {"command": "git reset --hard HEAD~3"}),
    ("Read", {"file_path": str(HOME / ".aws" / "credentials")}), ("Read", {"file_path": "/tmp/project/.env"}),
    ("Read", {"file_path": "/tmp/project/README.md"}), ("Write", {"file_path": "/tmp/project/.env.example", "content": "KEY=\n"}),
    ("Edit", {"file_path": "/tmp/project/app.py", "old_string": "a", "new_string": "b"}),
]
DENY_EXTRA = ["Edit({dest}/hooks/**)", "Write({dest}/hooks/**)", "Edit({dest}/settings.json)", "Write({dest}/settings.json)",
              "Edit({dest}/engine/**)", "Write({dest}/engine/**)", "Edit({dest}/rules/**)", "Write({dest}/rules/**)",
              "Read({nh}/keys/**)", "Edit({nh}/**)", "Write({nh}/**)"]


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def substitute(text, subs, label):
    for old, new in subs:
        if old not in text:
            raise SystemExit(f"vendor stopped: {label} no longer contains {old[:60]!r}; update the substitution")
        text = text.replace(old, new)
    return text


def decision(script, tool, ti, log_dir):
    payload = json.dumps({"tool_name": tool, "tool_input": ti, "cwd": "/tmp/project", "hook_event_name": "PreToolUse"})
    env = {**os.environ, "SECRET_GUARD_LOG": str(log_dir / "s.jsonl"), "DESTRUCTIVE_GUARD_LOG": str(log_dir / "d.log")}
    r = subprocess.run([sys.executable, str(script), "--mode", "claude"], input=payload, capture_output=True, text=True, env=env, timeout=30)
    denied = r.returncode == 2 or '"deny"' in r.stdout
    asked = '"ask"' in r.stdout
    return "deny" if denied else "ask" if asked else "allow"


def vendor():
    src_hooks = HOME / ".claude" / "hooks"
    # pack/vendor/pstack comes from `vendor-pstack` and a reviewed clone; refreshing the owner's files must not remove it.
    if (VENDOR / "guards").exists():
        shutil.rmtree(VENDOR / "guards")
    (VENDOR / "guards").mkdir(parents=True)
    record = {"guards": {}, "skills": {}, "deny": None}
    for name, subs in GUARDS.items():
        text = substitute((src_hooks / name).read_text(), subs, name)
        (VENDOR / "guards" / name).write_text(text)
        record["guards"][name] = {"source_sha256": sha(src_hooks / name), "substitutions": len(subs)}
    for name, subs in SKILLS.items():
        src = HOME / ".claude" / "skills" / name
        target = PACKS / SKILL_PACK.get(name, "core") / "skills" / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "agents"))
        skill_md = target / "SKILL.md"
        skill_md.write_text(substitute(skill_md.read_text(), subs, f"{name}/SKILL.md"))
        record["skills"][name] = {"source_sha256": sha(src / "SKILL.md"), "substitutions": len(subs)}
    deny = [d for d in json.load(open(HOME / ".claude" / "settings.json"))["permissions"]["deny"] if not any(p in d for p in PERSONAL_DENY)]
    json.dump(deny, open(VENDOR / "deny.json", "w"), indent=1)
    record["deny"] = len(deny)
    json.dump(record, open(VENDOR / "SOURCE.json", "w"), indent=1)
    with tempfile.TemporaryDirectory() as tmp:
        mismatches = []
        for name in GUARDS:
            for tool, ti in PROBES:
                a, b = decision(src_hooks / name, tool, ti, pathlib.Path(tmp)), decision(VENDOR / "guards" / name, tool, ti, pathlib.Path(tmp))
                if a != b:
                    mismatches.append((name, tool, ti, a, b))
    for m in mismatches:
        print("MISMATCH", m)
    print(f"vendored {len(GUARDS)} guards, {len(SKILLS)} skills, {len(deny)} deny rules; "
          f"{len(PROBES) * len(GUARDS)} guard decisions compared, {len(mismatches)} differ")
    return 1 if mismatches else 0


PSTACK_URL = "https://github.com/michael-denyer/pstack-claude"


def vendor_pstack(clone):
    """Copy pstack (MIT) from a reviewed local clone: skills, agents and licences, flattened for a direct install."""
    clone = pathlib.Path(clone).resolve()
    plug = clone / "plugins" / "pstack"
    commit = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    out = VENDOR / "pstack"
    if out.exists():
        shutil.rmtree(out)
    ign = shutil.ignore_patterns("node_modules", "__pycache__", ".DS_Store")
    shutil.copytree(plug / "skills", out / "skills", ignore=ign)
    (out / "agents").mkdir(parents=True)
    for d in ("agents", "effort-agents"):
        for f in sorted((plug / d).glob("*.md")):
            shutil.copy2(f, out / "agents" / f.name)
    for f in ("LICENSE", "LICENSE-cursor-team-kit", "NOTICE.md", "NOTICE-skills.md"):
        shutil.copy2(clone / f, out / f)
    shutil.copy2(plug / "models.json", out / "models.json")
    rewrites = 0
    for f in list((out / "skills").rglob("*.md")) + list((out / "agents").glob("*.md")):
        t = f.read_text()
        n = t.replace("pstack:", "").replace("under the installed plugin", "in the installed skills folder (the folder that holds `poteto-mode`)")
        n = n.replace("skills/poteto-mode/scripts/", "poteto-mode/scripts/").replace("${CLAUDE_PLUGIN_ROOT}/skills/", "")
        if n != t:
            f.write_text(n)
            rewrites += 1
    (out / "SOURCE.json").write_text(json.dumps({"source": PSTACK_URL, "commit": commit, "license": "MIT (see LICENSE, NOTICE.md)",
        "vendored": time.strftime("%Y-%m-%d"), "changes": "plugin namespace 'pstack:' removed and plugin-root paths pointed at the "
        "skills folder, because the harness installs skills directly, not as a plugin; the session hook's routing text lives in "
        "rules/pstack-routing.md"}, indent=2))
    skills = sorted(p.name for p in (out / "skills").iterdir() if (p / "SKILL.md").exists())
    print(f"vendored pstack {commit[:8]}: {len(skills)} skills, {len(list((out / 'agents').glob('*.md')))} agents, {rewrites} files rewritten")
    return 0


def build(dest, packs=PACK_NAMES):
    packs = ["core"] + [p for p in packs if p in PACK_NAMES and p != "core"]
    dest = pathlib.Path(dest).expanduser().resolve()
    if not (VENDOR / "SOURCE.json").exists():
        raise SystemExit("packs/core/vendor is missing; the owner runs `python3 team.py vendor` first")
    if dest.exists():
        for p in dest.rglob("*"):
            p.chmod(p.stat().st_mode | stat.S_IWUSR | (stat.S_IXUSR if p.is_dir() else 0))
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    ign = shutil.ignore_patterns("tests", "__pycache__", "*.pyc", ".pytest_cache", ".DS_Store", "*.log", "*.jsonl")
    shutil.copytree(PACK / "hooks", dest / "hooks", ignore=ign)
    for g in (VENDOR / "guards").iterdir():
        if g.is_file() and g.suffix == ".py" and not g.name.startswith("test_"):
            shutil.copy2(g, dest / "hooks" / g.name)
    shutil.copytree(PACK / "tools", dest / "tools", ignore=ign)
    src_note = dest / "tools" / "phi_guard.SOURCE.json"
    if src_note.exists():  # provenance without the owner's home path
        meta = json.load(open(src_note))
        meta["source"] = "nodaris agent-os: compliance/phi_guard.py"
        json.dump(meta, open(src_note, "w"), indent=2)
    shutil.copytree(PACK / "agents", dest / "agents")
    if (VENDOR / "pstack").exists():
        (dest / "licenses" / "pstack").mkdir(parents=True)
        for f in ("LICENSE", "LICENSE-cursor-team-kit", "NOTICE.md", "NOTICE-skills.md", "SOURCE.json"):
            shutil.copy2(VENDOR / "pstack" / f, dest / "licenses" / "pstack" / f)
    shutil.copytree(PACK / "output-styles", dest / "output-styles")
    (dest / "skills").mkdir()
    for pack in packs:
        for p in sorted((PACKS / pack / "skills").iterdir()):
            if p.is_dir():
                shutil.copytree(p, dest / "skills" / p.name, ignore=ign)
    (dest / "packs.json").write_text(json.dumps({"packs": packs}) + "\n")
    for skill_md in (dest / "skills").rglob("SKILL.md"):
        skill_md.write_text(skill_md.read_text().replace("<harness>", str(dest)))
    shutil.copytree(ENGINE, dest / "engine" / "nodaris_harness", ignore=ign)
    shutil.copytree(HERE / "bin", dest / "bin", ignore=ign)
    shutil.copytree(HERE / "rules", dest / "rules", ignore=ign)
    (dest / "CLAUDE.md").write_text((HERE / "rules" / "RULES.md").read_text().replace("`nodaris-harness ", f"`{dest / 'bin' / 'nodaris-harness'} "))
    sys.path.insert(0, str(HERE / "engine"))
    from nodaris_harness import hosts
    settings = {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "permissions": {"deny": json.load(open(VENDOR / "deny.json")) + [d.format(dest="/" + str(dest), nh=NH_RULE) for d in DENY_EXTRA]},
        "env": {"ENABLE_TOOL_SEARCH": "true"},
        "attribution": {"commit": "", "pr": ""},
        "outputStyle": "Engineer",
    }
    settings["hooks"] = hosts._hooks_json("claude", None, str(dest))["hooks"]
    needed = [dest / "bin" / "nodaris-harness", dest / "engine" / "nodaris_harness" / "dispatch.py", dest / "rules" / "policy.json",
              dest / "tools" / "scan.py", dest / "tools" / "trust_report.py"]
    needed += [dest / "hooks" / n for n in ("secret-guard.py", "destructive-guard.py", "intake.py", "tracker.py", "phi_lint.py", "done_gate.py")]
    missing = [str(x) for x in needed if not x.is_file()]
    if missing:
        shutil.rmtree(dest)
        raise SystemExit(f"build stopped, nothing installed: missing {missing}")
    json.dump(settings, open(dest / "settings.json", "w"), indent=1)
    launch = dest / "launch.sh"
    bin_dir = dest / "bin"
    launch.write_text(f'#!/bin/sh\n# Start Claude Code with the healthcare engineering harness.\nPATH="{bin_dir}:$PATH" CLAUDE_CONFIG_DIR="{dest}" exec claude "$@"\n')
    launch.chmod(0o755)
    shutil.copy2(HERE / "INSTALL.md", dest / "README.md")
    for p in [*(dest / "hooks").iterdir(), *(dest / "engine").rglob("*"), *(dest / "rules").iterdir(), dest / "settings.json"]:
        if p.is_file():
            p.chmod(0o444)
    print(f"team harness at {dest}: {len(list((dest / 'skills').iterdir()))} skills, "
          f"{sum(len(es) for es in settings['hooks'].values())} hook entries, {len(settings['permissions']['deny'])} deny rules, "
          "every hook and tool present")
    print(f"start it with: {launch}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "vendor-pstack":
        sys.exit(vendor_pstack(sys.argv[2]))
    if len(sys.argv) >= 2 and sys.argv[1] == "vendor":
        sys.exit(vendor())
    if len(sys.argv) in (3, 4) and sys.argv[1] == "build":
        sys.exit(build(sys.argv[2], sys.argv[3].split(",") if len(sys.argv) == 4 else PACK_NAMES))
    print(__doc__)
    sys.exit(2)
