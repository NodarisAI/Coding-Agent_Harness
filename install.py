#!/usr/bin/env python3
"""Install the Nodaris coding-agent harness: onboarding, then each chosen coding agent, then a check.

  python3 install.py                     ask the onboarding questions, show each change, install and check
  python3 install.py --dry-run --yes --answers '<json>'   show what would change and write nothing

Answers JSON keys (values or the option labels shown in the questions):
  company        your company name (required)
  is_nodaris     yes or no
  use            list of healthcare, creative, general
  role           engineer, builder or practice-staff
  hosts          list of claude, codex, gemini, cursor, opencode
  reply_style    brief, explain or teach
  plan           pro, max, team or api
  team_sync      yes or no (Nodaris team members only)
  packs          optional list of core, healthcare, creative (derived from use when absent)
  subagent_budget_tokens   optional whole number (set from the plan when absent)
"""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("The harness needs Python 3.9 or newer, and this is Python %d.%d. Install a newer Python 3 "
                     "and run this command again with it.\n" % sys.version_info[:2])
    sys.exit(1)

import argparse, os, re, subprocess  # noqa: E402

ROOT = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(ROOT, "engine"))

HOSTS = ("claude", "codex", "gemini", "cursor", "opencode")


def parse(argv=None):
    ap = argparse.ArgumentParser(prog="install.py", description="Install the Nodaris coding-agent harness.",
                                 epilog=__doc__.split("\n\n", 2)[2], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="no prompts; for an agent after the person approved in chat")
    ap.add_argument("--host", choices=HOSTS, help="install for this coding agent only")
    ap.add_argument("--packs", help="comma-separated packs to use instead of the onboarding choice")
    ap.add_argument("--answers", help="onboarding answers as JSON, or a path to a JSON file")
    ap.add_argument("--dry-run", action="store_true", help="show what would change and write nothing")
    ap.add_argument("--uninstall", action="store_true", help="remove the harness from every coding agent it was installed in")
    ap.add_argument("--reconfigure", action="store_true", help="ask the onboarding questions again")
    ap.add_argument("--no-motion", action="store_true", help="plain output with no animation")
    return ap.parse_args(argv)


def summarise(diff_text):
    """Per file: (path, lines added, lines removed) from a unified diff."""
    files, cur = [], None
    for ln in diff_text.splitlines():
        if ln.startswith("+++ "):
            cur = [ln[4:].strip(), 0, 0]
            files.append(cur)
        elif ln.startswith("--- ") or ln.startswith("@@"):
            continue
        elif cur and ln.startswith("+"):
            cur[1] += 1
        elif cur and ln.startswith("-"):
            cur[2] += 1
    return files


def short(path):
    h = os.path.expanduser("~")
    return "~" + path[len(h):] if h and h != "/" and (path == h or path.startswith(h + os.sep)) else path


def installed_hosts(home):
    d = os.path.join(home, "installs")
    if not os.path.isdir(d):
        return []
    return sorted(n[:-5] for n in os.listdir(d) if n.endswith(".json") and n[:-5] in HOSTS + ("git",))


def uninstall(a, tui, hosts, policy):
    found = installed_hosts(policy.home())
    if not found:
        tui.line("The harness is not installed in any coding agent on this machine.")
        return 0
    tui.panel("Uninstall", ["The harness will be removed from: " + ", ".join(found) + ".",
                            "Files it changed are restored from their backups when nothing else changed them.",
                            "Your settings and lessons in " + policy.home() + " are kept."])
    if a.dry_run:
        tui.line("Dry run: nothing was removed.")
        return 0
    if not a.yes and not tui.confirm("Remove the harness now?", False):
        tui.line("Nothing was removed.")
        return 1
    from nodaris_harness import onboard
    onboard.forget_offer()
    for host in found:
        rep = hosts.uninstall(host)
        tui.line("%s: %d item(s) removed or restored." % (host, len(rep.get("removed", []))))
        for k in rep.get("kept", []):
            tui.line("  " + k)
    return 0


def settings_for(a, tui, onboard):
    """Settings from --answers, an earlier onboarding, or the questions. None when the person stopped."""
    if a.answers:
        s = onboard.build(onboard.parse_answers(a.answers))
        if not a.dry_run:
            onboard.save(s)
        return s
    if onboard.is_onboarded() and not a.reconfigure:
        return onboard.load()
    if a.yes or not tui.can_prompt():
        raise onboard.SettingsError("answers", "are needed: pass --answers, or run python3 install.py in a terminal")
    return onboard.interactive(show_splash=False, save_settings=not a.dry_run)


def main(argv=None):
    a = parse(argv)
    if a.no_motion:
        os.environ["NODARIS_REDUCED_MOTION"] = "1"
    from nodaris_harness import hosts, onboard, policy, tui

    if a.uninstall:
        return uninstall(a, tui, hosts, policy)
    if not a.yes:
        tui.splash()
    try:
        settings = settings_for(a, tui, onboard)
        if settings is None:
            return 1
        if a.packs:
            chosen = [p for p in re.split(r"[,\s]+", a.packs) if p]
            settings["packs"] = ["core"] + [p for p in chosen if p != "core"]
            onboard.validate(settings)
            if not a.dry_run:
                onboard.save(settings)
    except onboard.SettingsError as e:
        sys.stderr.write("Setup stopped. %s: %s.\n" % (e.field, str(e).split(": ", 1)[1]))
        return 2
    except KeyboardInterrupt:
        tui.line("\nSetup was cancelled. Nothing further was changed.")
        return 130

    targets = [a.host] if a.host else list(settings["hosts"])
    skill_count = len(hosts.skill_sources(packs=settings["packs"]))
    tui.line("Packs: %s. Coding agents: %s." % (", ".join(settings["packs"]), ", ".join(targets)))
    done, failed = [], []
    for host in targets:
        rep = hosts.install(host, dry_run=True)
        files = summarise(rep["diff"])
        has_skills = "skills" in hosts.paths(host)
        body = ["%s  (+%d, -%d lines)" % (short(p), add, rem) for p, add, rem in files] or ["No configuration changes."]
        if has_skills:
            body.append("%d skill folders copied into %s" % (skill_count, short(hosts.paths(host)["skills"])))
        body.append("Enforcement: " + rep["level"] + ".")
        tui.panel("Changes for " + host, body)
        if a.dry_run:
            if rep["diff"]:
                sys.stdout.write(rep["diff"])
            tui.line("Dry run: nothing was written for %s." % host)
            continue
        try:
            if not a.yes and not tui.confirm("Install the harness for %s?" % host, True):
                tui.line("Skipped %s." % host)
                continue
        except KeyboardInterrupt:
            tui.line("\nSetup was cancelled. Agents already installed stay installed.")
            break
        with tui.Spinner("Installing for " + host):
            out = hosts.install(host)
        for s in out.get("skipped", []):
            tui.line("  " + s)
        with tui.Spinner("Checking " + host) as sp:
            p = subprocess.run([sys.executable, hosts.bin_path(), "doctor", "--host", host], capture_output=True,
                               text=True, timeout=600)
            sp.result = "all checks passed" if p.returncode == 0 else "some checks failed"
        (done if p.returncode == 0 else failed).append(host)
        if p.returncode != 0:
            tui.line(p.stdout.rstrip())

    if a.dry_run:
        tui.line("Run the same command without --dry-run to install.")
        return 0
    binp = hosts.bin_path()
    lines = []
    if done:
        lines.append("Installed and checked for " + ", ".join(done) + ".")
    if failed:
        lines.append("The check failed for " + ", ".join(failed) + "; the output is shown above.")
    lines += ["", "Commands you will use next:",
              "  %s watch --split     live panel: tokens burning, subagents, memories and files" % binp,
              "  %s approve --list    see actions waiting for your approval" % binp,
              "  %s receipt           what the last session proved" % binp,
              "  %s doctor --host %s  check the install again" % (binp, (done or targets or ["claude"])[0]),
              "", "To remove the harness, run python3 install.py --uninstall."]
    tui.panel("Finished" if not failed else "Finished with problems", lines)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\nSetup was cancelled.\n")
        sys.exit(130)
