#!/usr/bin/env python3
"""Trust receipt: what a session actually proved, from recorded evidence only.

  python3 trust_report.py              # the most recent session
  python3 trust_report.py SESSION_ID   # a given session
  python3 trust_report.py --list       # recent sessions

Reads the state the harness hooks record (edits, checks and their results, PHI lint findings, done-gate stops).
It never reads the model's summary, so nothing here is a claim the model made about itself.
"""
import glob, json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
import hlib
import done_gate

REASON = {"tests": "code changed after the last passing check", "failing": "the last check failed",
          "attack": "security-sensitive code changed without adversarial tests that ran",
          "scan": "no clean security scan after the last sensitive change",
          "threat": "no threat model note for the security-sensitive change"}


def sessions():
    files = [f for f in glob.glob(os.path.join(hlib.STATE_DIR, "*.json"))]
    return sorted(files, key=os.path.getmtime, reverse=True)


def repo_root(paths):
    """The repository the change lives in, so paths read as the repository shows them."""
    if not paths:
        return None
    d = os.path.dirname(os.path.commonpath(paths)) if len(paths) == 1 else os.path.commonpath(paths)
    while d and d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return None


def render(sid, state):
    ev = state.get("events", [])
    edits = [e for e in ev if e["kind"] == "edit"]
    checks = [e for e in ev if e["kind"] == "check"]
    lints = [e for e in ev if e["kind"] == "lint"]
    blocks = [e for e in ev if e["kind"] == "block"]
    reviews = [e for e in ev if e["kind"] == "agent" and "review" in e.get("agent", "")]
    last_edit = max((e["seq"] for e in edits), default=0)
    passed_after = [c for c in checks if c["ok"] and c["seq"] > last_edit]
    sensitive = sorted({e["path"] for e in edits if e.get("sensitive")})
    attacks = sorted({e["path"] for e in edits if e.get("attack")})
    attack_seq = max((e["seq"] for e in edits if e.get("attack")), default=None)
    attacks_ran = attack_seq is not None and any(c["ok"] and c.get("test") and c.get("ran", True) and c["seq"] > attack_seq for c in checks)
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(ev[0]["t"])) if ev else "unknown"
    out = [f"# Trust receipt, session {sid[:8]}", "", f"Started {started}. Built from what the hooks recorded, not from the model's summary.", ""]
    verdicts = []
    if not edits:
        verdicts.append("No code was changed in this session.")
    else:
        verdicts.append(("Proven: " if passed_after else "Not proven: ") + ("checks passed after the last code change." if passed_after else "no check passed after the last code change."))
        if sensitive:
            verdicts.append(("Proven: " if attacks_ran else "Not proven: ") + ("adversarial tests were written for the security-sensitive change and ran." if attacks_ran else "the security-sensitive change has no adversarial tests that ran."))
        if sensitive:
            clean = not done_gate.unscanned(state)
            verdicts.append(("Proven: " if clean else "Not proven: ") + ("a clean security scan covered every sensitive file after its last change." if clean else "not every sensitive file was covered by a clean security scan after its last change."))
            threat = [e for e in ev if e["kind"] == "doc" and e.get("threat")]
            unnamed = done_gate.unnamed_in_threat_model(state)
            verdicts.append(("Done: " if threat and not unnamed else "Not done: ") + "a threat model note that names every sensitive file"
                            + (f" ({os.path.basename(threat[-1]['path'])})." if threat and not unnamed else
                               f" (not named: {', '.join(os.path.basename(p) for p in unnamed[:5])})." if threat else "."))
            verdicts.append(("Done: " if reviews else "Not done: ") + "an independent security review of the change" + (f" ({len(reviews)} run)." if reviews else "."))
        open_lint = [l for l in lints]
        if open_lint:
            verdicts.append(f"PHI lint raised {sum(len(l['findings']) for l in lints)} finding(s) while the code was written; each was reported to the model at the moment of writing.")
    out += ["## Verdict", ""] + [f"- {v}" for v in verdicts] + [""]
    root = repo_root(sorted({e["path"] for e in edits}))
    rel = (lambda q: os.path.relpath(q, root)) if root else (lambda q: q)
    if root:
        out += [f"Repository: `{root}`", ""]
    out += ["## Code changed", ""] + ([f"- `{rel(p)}`" + ("  (security-sensitive)" if p in sensitive else "") + ("  (adversarial tests)" if p in attacks else "")
                                      for p in sorted({e['path'] for e in edits})] or ["- none"]) + [""]
    out += ["## Checks that ran", ""] + ([f"- {'passed' if c['ok'] else 'FAILED'}: `{c['cmd']}`" for c in checks] or ["- none"]) + [""]
    if lints:
        out += ["## PHI lint findings", ""] + [f"- `{os.path.basename(l['path'])}`: {f}" for l in lints for f in l["findings"]] + [""]
    if blocks:
        out += ["## Times the done gate stopped the model", ""] + [f"- {', '.join(REASON.get(r, r) for r in b['reasons'])}" for b in blocks] + [""]
    return "\n".join(out)


def main(argv):
    files = sessions()
    if "--list" in argv:
        for f in files[:15]:
            st = json.load(open(f))
            print(os.path.basename(f)[:-5], time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(f))),
                  f"{sum(1 for e in st.get('events', []) if e['kind'] == 'edit')} edits")
        return 0
    if argv:
        path = os.path.join(hlib.STATE_DIR, argv[0] + ".json")
    elif files:
        path = files[0]
    else:
        print("No harness sessions recorded yet.", file=sys.stderr)
        return 1
    if not os.path.exists(path):
        print(f"No recorded session {argv[0]}.", file=sys.stderr)
        return 1
    print(render(os.path.basename(path)[:-5], json.load(open(path))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
