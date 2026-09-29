"""What a person sets up for themselves before the harness is fully working: accounts, sign-ins and git.

Each check says what it found and, when something is missing, the exact command that fixes it. Nothing here
changes the machine; `nodaris-harness setup-check` only reads.
"""
import json, os, shutil, subprocess

from . import onboard, policy

ORG_REPO = "NodarisAI/Coding-Agent_Harness"
MIN_CLAUDE = (2, 1, 0)


def _run(cmd, runner, timeout=20):
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, "", ""


def _version(text):
    import re
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def checks(settings=None, which=shutil.which, runner=subprocess.run):
    """[(title, ok, detail, fix)]; fix is None when nothing is needed."""
    s = settings if settings is not None else (onboard.load() or {})
    nodaris = bool(s.get("is_nodaris"))
    hosts = s.get("hosts") or ["claude"]
    out = []

    if "claude" in hosts:
        if not which("claude"):
            out.append(("Claude Code command line", False, "The `claude` command is not installed.",
                        "Install it: curl -fsSL https://claude.ai/install.sh | bash"))
        else:
            v = _version(_run(["claude", "--version"], runner)[1])
            ok = v is not None and v >= MIN_CLAUDE
            out.append(("Claude Code command line", ok,
                        "Version %s." % ".".join(map(str, v)) if v else "The version could not be read.",
                        None if ok else "Update it: claude update"))
            code, text, _ = _run(["claude", "auth", "status"], runner)
            try:
                signed = code == 0 and bool(json.loads(text).get("loggedIn"))
            except ValueError:
                signed = False
            out.append(("Claude Code sign-in", signed,
                        "Signed in." if signed else "Not signed in.",
                        None if signed else "Run `claude` once and sign in with your Claude account (or run: claude auth login)."))

    code, name, _ = _run(["git", "config", "--get", "user.name"], runner)
    code2, mail, _ = _run(["git", "config", "--get", "user.email"], runner)
    ok = bool(name) and bool(mail)
    out.append(("Git identity", ok, "Commits are signed as %s <%s>." % (name, mail) if ok else "Git does not know your name and email.",
                None if ok else 'Run: git config --global user.name "Your Name" && git config --global user.email "you@nodaris.ai"'))

    if not which("gh"):
        out.append(("GitHub command line", False, "The `gh` command is not installed.",
                    "Install it (on a Mac: brew install gh), then run: gh auth login"))
    else:
        code, _, _ = _run(["gh", "auth", "status", "--hostname", "github.com"], runner)
        out.append(("GitHub sign-in", code == 0, "Signed in to GitHub." if code == 0 else "Not signed in to GitHub.",
                    None if code == 0 else "Run: gh auth login (choose GitHub.com, HTTPS, and sign in with your browser)"))
        if nodaris and code == 0:
            rc, _, _ = _run(["gh", "repo", "view", ORG_REPO, "--json", "name"], runner)
            out.append(("Access to NodarisAI repositories", rc == 0,
                        "Your GitHub account can read NodarisAI repositories." if rc == 0 else
                        "Your GitHub account cannot read NodarisAI repositories.",
                        None if rc == 0 else "Ask a Nodaris admin to add your GitHub account to the NodarisAI organization."))

    if nodaris and s.get("jev", True):
        from . import jev
        st = jev.status()
        if st["key"]:
            out.append(("Jev team key", True, "Saved in %s, readable only by you." % st["key_path"], None))
        elif not which("aws"):
            out.append(("Jev team key", False, "Jev needs the team key, and the AWS command line tool is not installed.",
                        "Install it (on a Mac: brew install awscli), run `aws configure sso` once, then run: "
                        "nodaris-harness jev fetch-key"))
        else:
            out.append(("Jev team key", False, "The team key has not been fetched yet.",
                        "Run `aws sso login` if you are not signed in, then run: nodaris-harness jev fetch-key"))

    git_home = os.path.join(policy.home(), "installs", "git.json")
    out.append(("Git hooks in your repositories", os.path.exists(git_home),
                "Installed in at least one repository." if os.path.exists(git_home) else
                "Not installed in any repository yet. The agent is already stopped from pushing to protected "
                "branches; the git hooks also check your own commits and pushes.",
                None if os.path.exists(git_home) else
                "In each repository you push from, run: nodaris-harness install --host git --project ."))
    return out


def render(items):
    lines = []
    for title, ok, detail, fix in items:
        lines.append(("  ok    " if ok else "  todo  ") + title + ": " + detail)
        if fix:
            lines.append("          " + fix)
    left = sum(1 for _, ok, _, _ in items if not ok)
    lines.append("")
    lines.append("Everything is set up." if not left else
                 "%d item(s) left. Run `nodaris-harness setup-check` again after fixing them." % left)
    return lines
