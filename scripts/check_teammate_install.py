#!/usr/bin/env python3
"""Install the harness the way a new teammate does, in a throwaway home folder, and prove each step.

  python3 scripts/check_teammate_install.py [--source URL_OR_PATH] [--branch NAME] [--python PYTHON]

Clones the source, installs for Claude Code with onboarding answers, checks the settings it wrote, checks that
onboarding is offered once on a machine that skipped it and never on one that finished it, runs the doctor, then
uninstalls and checks the person's own settings file comes back byte for byte. Nothing outside a temporary folder
is touched. Exit code 0 means every step passed.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile

REPO = "https://github.com/NodarisAI/Coding-Agent_Harness.git"
ANSWERS = {"company": "Example Health", "is_nodaris": "No", "use": ["Healthcare apps", "Websites and motion"], "role": "Engineer",
           "hosts": ["Claude Code"], "reply_style": "Explain", "plan": "Max", "graphs": "No", "team_sync": "No"}
OWN_SETTINGS = {"theme": "dark", "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(curl:*)"]}}


def step(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if detail and not ok else ""))
    return ok


def hook(py, src, env, payload):
    p = subprocess.run([py, os.path.join(src, "bin", "nodaris-harness"), "hook", "--host", "claude"],
                       input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=120)
    return p.stdout


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=REPO)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--python", default="/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable)
    a = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="harness-teammate-")
    ok = True
    try:
        src = os.path.join(tmp, "src")
        clone = ["git", "clone", "--quiet", "--depth", "1"]
        if not os.path.isdir(a.source):
            clone += ["--branch", a.branch]
        r = subprocess.run(clone + ["--", a.source, src], capture_output=True, text=True, timeout=600)
        if not step(f"clone {a.source}" + ("" if os.path.isdir(a.source) else f" at {a.branch}"), r.returncode == 0,
                    r.stderr.strip()[-200:]):
            return 1
        home = os.path.join(tmp, "home")
        os.makedirs(os.path.join(home, ".claude"))
        own = os.path.join(home, ".claude", "settings.json")
        with open(own, "w") as fh:
            json.dump(OWN_SETTINGS, fh, indent=2)
        before = open(own, "rb").read()
        env = dict(os.environ, HOME=home, NODARIS_HARNESS_HOME=os.path.join(home, ".nodaris-harness"),
                   NODARIS_HARNESS_NO_BG="1", NODARIS_REDUCED_MOTION="1")
        ver = subprocess.run([a.python, "--version"], capture_output=True, text=True).stdout.strip()
        r = subprocess.run([a.python, os.path.join(src, "install.py"), "--yes", "--no-motion", "--host", "claude",
                            "--answers", json.dumps(ANSWERS)], capture_output=True, text=True, env=env, timeout=900)
        ok &= step(f"install for Claude Code with {ver}", r.returncode == 0, (r.stdout + r.stderr)[-400:])
        data = json.load(open(own))
        hooks = json.dumps(data.get("hooks") or {})
        ok &= step("hooks written for every event", all(e in hooks for e in ("PreToolUse", "UserPromptSubmit", "Stop",
                                                                             "SessionStart", "SubagentStop")))
        deny = (data.get("permissions") or {}).get("deny") or []
        ok &= step("deny rules added next to the person's own", "Bash(curl:*)" in deny and "Read(~/.aws/**)" in deny)
        ok &= step("the person's own settings kept", data.get("theme") == "dark")
        ok &= step("status line added", "statusline" in json.dumps(data.get("statusLine") or {}))
        skills = os.path.join(home, ".claude", "skills")
        ok &= step("skills copied", os.path.isdir(os.path.join(skills, "spec-first")) and
                   os.path.isdir(os.path.join(skills, "healthcare-domain")))
        start = {"hook_event_name": "SessionStart", "session_id": "t1", "cwd": home, "source": "startup"}
        ok &= step("an onboarded person is never offered onboarding", "has not finished onboarding" not in hook(a.python, src, env, start))
        fresh = dict(env, NODARIS_HARNESS_HOME=os.path.join(home, ".fresh-harness"))
        first = hook(a.python, src, fresh, start)
        again = hook(a.python, src, fresh, dict(start, session_id="t2"))
        ok &= step("a machine that skipped onboarding is offered it once", "has not finished onboarding" in first and
                   "has not finished onboarding" not in again)
        r = subprocess.run([a.python, os.path.join(src, "bin", "nodaris-harness"), "doctor", "--host", "claude"],
                           capture_output=True, text=True, env=env, timeout=600)
        ok &= step("doctor passes", r.returncode == 0, r.stdout[-400:])
        r = subprocess.run([a.python, os.path.join(src, "install.py"), "--uninstall", "--yes", "--no-motion"],
                           capture_output=True, text=True, env=env, timeout=600)
        ok &= step("uninstall", r.returncode == 0, (r.stdout + r.stderr)[-300:])
        ok &= step("the person's settings file is back byte for byte", open(own, "rb").read() == before)
        ok &= step("skills removed", not os.path.isdir(os.path.join(skills, "spec-first")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("All steps passed." if ok else "Some steps failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
