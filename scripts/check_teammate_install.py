#!/usr/bin/env python3
"""Install the harness the way a new teammate does, in a throwaway home folder, and prove each step.

  python3 scripts/check_teammate_install.py [--source URL_OR_PATH] [--branch NAME] [--python PYTHON]

Clones the source, installs for Claude Code with onboarding answers, checks the settings it wrote, checks that
onboarding is offered once on a machine that skipped it and never on one that finished it, runs the doctor, then
uninstalls and checks the person's own settings file comes back byte for byte. Nothing outside a temporary folder
is touched. Then installs again as a Nodaris team member with Jev on, against a stand-in `aws` command and a local
stand-in for the Jev service, and checks the key handling, the restart notice, the setup checklist and that a
message with patient identifiers never reaches Jev. Exit code 0 means every step passed.
"""
import argparse, http.server, json, os, shutil, stat, subprocess, sys, tempfile, threading

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

FAKE_KEY = "sk-or-v1-" + "e2e0" * 12
NODARIS = dict(ANSWERS, company="Nodaris", is_nodaris="Yes", use=["Healthcare apps"], jev="Yes")
JEV_REPLY = {"answers": {"type": {"choice": "fix", "confidence": 0.9}, "effort": {"choice": "medium", "confidence": 0.8},
                         "shape_reply": {"choice": "explain", "probabilities": {"explain": 0.8}}, "critical": {"noul": 0.1}},
             "usage": {"cost": 0.0001}}


def jev_stand_in():
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            out = json.dumps(JEV_REPLY).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def nodaris_member(a, src, tmp):
    """A Nodaris team member: Jev on, the team key fetched through their own AWS sign-in."""
    ok = True
    home = os.path.join(tmp, "member")
    stub = os.path.join(tmp, "stub-bin")
    os.makedirs(os.path.join(home, ".claude"))
    os.makedirs(stub)
    with open(os.path.join(stub, "aws"), "w") as fh:
        fh.write("#!/bin/sh\n[ \"$1 $2\" = \"secretsmanager get-secret-value\" ] || exit 2\necho %s\n" % FAKE_KEY)
    os.chmod(os.path.join(stub, "aws"), 0o755)
    srv, seen = jev_stand_in()
    try:
        hh = os.path.join(home, ".nodaris-harness")
        env = dict(os.environ, HOME=home, NODARIS_HARNESS_HOME=hh, NODARIS_HARNESS_NO_BG="1", NODARIS_REDUCED_MOTION="1",
                   PATH=stub + os.pathsep + os.environ.get("PATH", ""),
                   NODARIS_JEV_URL="http://127.0.0.1:%d/api/alpha/decisions" % srv.server_port)
        r = subprocess.run([a.python, os.path.join(src, "install.py"), "--yes", "--no-motion", "--host", "claude",
                            "--answers", json.dumps(NODARIS)], capture_output=True, text=True, env=env, timeout=900)
        out = r.stdout + r.stderr
        ok &= step("Nodaris member: install with Jev on", r.returncode == 0, out[-400:])
        key = os.path.join(hh, "secrets", "jev.key")
        ok &= step("Nodaris member: team key fetched through AWS into a private file",
                   os.path.exists(key) and stat.S_IMODE(os.stat(key).st_mode) == 0o600)
        ok &= step("Nodaris member: the key is never printed", FAKE_KEY not in out)
        ok &= step("Nodaris member: told to restart Claude Code (command line and desktop app)",
                   "claude --continue" in out and "Desktop app" in out)
        ok &= step("Nodaris member: setup checklist shown", "Before you start" in out or "setup-check" in out)
        prompt = {"hook_event_name": "UserPromptSubmit", "session_id": "m1", "cwd": home,
                  "prompt": "the export button on the billing page fails with a 500, find the cause and fix it"}
        ctx = hook(a.python, src, env, prompt)
        ok &= step("Nodaris member: Jev shapes a message", "Reply shape: explain" in ctx and len(seen) == 1, ctx[-300:])
        ok &= step("Nodaris member: the key is not in what Jev received", FAKE_KEY not in json.dumps(seen))
        phi = dict(prompt, session_id="m2", prompt="Patient John Smith, SSN 123-45-6789, DOB 03/14/1961 was denied, fix the claim")
        blocked = hook(a.python, src, env, phi)
        ok &= step("Nodaris member: a message with patient identifiers is blocked and never reaches Jev",
                   "patient information" in blocked and len(seen) == 1, blocked[-300:])
        r = subprocess.run([a.python, os.path.join(src, "bin", "nodaris-harness"), "jev", "status"],
                           capture_output=True, text=True, env=env, timeout=60)
        ok &= step("Nodaris member: jev status", r.returncode == 0 and "Team key: present" in r.stdout, r.stdout)
        r = subprocess.run([a.python, os.path.join(src, "bin", "nodaris-harness"), "setup-check"],
                           capture_output=True, text=True, env=env, timeout=120)
        ok &= step("Nodaris member: setup-check runs and lists the Jev key as done", "ok    Jev team key" in r.stdout, r.stdout[-400:])
        r = subprocess.run([a.python, os.path.join(src, "install.py"), "--uninstall", "--yes", "--no-motion"],
                           capture_output=True, text=True, env=env, timeout=600)
        ok &= step("Nodaris member: uninstall", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    finally:
        srv.shutdown()
    return ok


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
        link = os.path.join(home, ".local", "bin", "nodaris")
        ok &= step("the nodaris command is on the PATH", os.path.realpath(link) == os.path.realpath(os.path.join(src, "bin", "nodaris-harness")))
        fake = os.path.join(tmp, "fakebin")
        os.makedirs(fake)
        with open(os.path.join(fake, "claude"), "w") as fh:
            fh.write('#!/bin/sh\necho "claude started with: $*"\n')
        os.chmod(os.path.join(fake, "claude"), 0o755)
        r = subprocess.run([link, "--continue"], capture_output=True, text=True, timeout=60,
                           env=dict(env, PATH=fake + os.pathsep + env.get("PATH", "")))
        ok &= step("nodaris hands the terminal to Claude Code with its options", r.returncode == 0 and
                   "claude started with: --continue" in r.stdout, (r.stdout + r.stderr)[-300:])
        r = subprocess.run([a.python, os.path.join(src, "bin", "nodaris-harness"), "doctor", "--host", "claude"],
                           capture_output=True, text=True, env=env, timeout=600)
        ok &= step("doctor passes", r.returncode == 0, r.stdout[-400:])
        r = subprocess.run([a.python, os.path.join(src, "install.py"), "--uninstall", "--yes", "--no-motion"],
                           capture_output=True, text=True, env=env, timeout=600)
        ok &= step("uninstall", r.returncode == 0, (r.stdout + r.stderr)[-300:])
        ok &= step("the person's settings file is back byte for byte", open(own, "rb").read() == before)
        ok &= step("skills removed", not os.path.isdir(os.path.join(skills, "spec-first")))
        ok &= step("the nodaris command removed", not os.path.lexists(link))
        ok &= nodaris_member(a, src, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("All steps passed." if ok else "Some steps failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
