"""Route-aware gates: the router's playbook is checked at the end of the turn, not just suggested at the start.

Three checks, each tied to the kind of work the router named for the session:

- Investigation and bug fixes end with evidence: a file and line, a quoted command with its exit code, or quoted
  output. An answer without any is sent back once.
- A feature or new app that touches patient data, money, tenancy or authentication has a design record in
  docs/design/ written during the session, with at least two options, the chosen one and what was rejected.
- A push is shown with the review state of the exact commit being pushed. A review is bound to the commit and its
  tree; any new commit makes it stale.

Each end-of-turn check sends the agent back at most once per session, so a gate can never trap a session in a loop,
and a person's instruction always wins over it.
"""
import hashlib, json, os, re, subprocess, time

from . import policy

SENSITIVE = {"phi", "tenant-money", "auth"}
EVIDENCE = [
    re.compile(r"[\w./-]+\.[A-Za-z]{1,6}:\d+"),                      # path/to/file.py:42
    re.compile(r"\bexit(?:ed)?(?: with)?(?: code| status)?[ :=]*\d+\b", re.I),
    re.compile(r"\brc[ =:]+\d+\b"),
    re.compile(r"```"),
    re.compile(r"\b\d+ (passed|failed)\b"),
]
DESIGN_DIR = os.path.join("docs", "design")


def _sdir(session):
    d = os.path.join(policy.home(), "sessions", re.sub(r"[^\w-]", "_", session or "none"))
    os.makedirs(d, exist_ok=True)
    return d


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def save_route(session, decision):
    path = os.path.join(_sdir(session), "route.json")
    state = _read(path, {})
    state.setdefault("started", time.time())
    if decision.get("playbook"):
        state["playbook"], state["overlays"] = decision["playbook"], decision.get("overlays", [])
    _write(path, state)


def load_route(session):
    return _read(os.path.join(_sdir(session), "route.json"), {})


def has_evidence(text):
    return any(rx.search(text or "") for rx in EVIDENCE)


def design_ok(path):
    """A design record names at least two options, the chosen one and the rejected ones, and its acceptance checks."""
    try:
        text = open(path).read()
    except OSError:
        return False, "cannot be read"
    sections = {m.group(1).strip().lower() for m in re.finditer(r"^##\s+(.+)$", text, re.M)}
    missing = [s for s in ("options", "chosen", "rejected", "acceptance checks") if s not in sections]
    if missing:
        return False, "missing section(s): " + ", ".join(missing)
    body = text.split("## Options", 1)[-1].split("\n## ", 1)[0] if "## Options" in text else ""
    if len(re.findall(r"^###\s+\S", body, re.M)) < 2:
        return False, "the Options section needs at least two structurally different options, each under a ### heading"
    return True, "ok"


def _sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except OSError:
        return ""


def _design_reviews():
    d = os.path.join(policy.home(), "design-reviews")
    os.makedirs(d, exist_ok=True)
    return d


def record_design_review(path, verdict, findings, reviewer):
    """Bind a review verdict to the record's exact content. APPROVED cannot coexist with an open high or medium finding."""
    verdict = verdict.upper()
    if verdict not in ("APPROVED", "CHANGES_REQUESTED"):
        return None, "verdict must be APPROVED or CHANGES_REQUESTED"
    open_serious = [f for f in findings if str(f.get("severity", "")).lower() in ("high", "medium") and not f.get("resolved")]
    if verdict == "APPROVED" and open_serious:
        return None, f"cannot approve with {len(open_serious)} open high or medium finding(s)"
    digest = _sha(path)
    if not digest:
        return None, "the design record cannot be read"
    rec = {"file": os.path.abspath(path), "sha256": digest, "verdict": verdict, "findings": findings[:50],
           "reviewer": reviewer, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _write(os.path.join(_design_reviews(), digest + ".json"), rec)
    return rec, "ok"


def design_approved(path):
    rec = _read(os.path.join(_design_reviews(), _sha(path) + ".json"), None)
    return bool(rec and rec["verdict"] == "APPROVED")


def design_records(repo, since):
    d = os.path.join(repo, DESIGN_DIR)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.endswith(".md") and os.path.getmtime(os.path.join(d, f)) >= since - 1]


def _git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _reviews_dir(repo):
    top = _git(repo, "rev-parse", "--show-toplevel") or os.path.abspath(repo)
    d = os.path.join(policy.home(), "reviews", hashlib.sha256(top.encode()).hexdigest()[:16])
    os.makedirs(d, exist_ok=True)
    return d


def record_review(repo, verdict, reviewer, findings=0, notes=""):
    commit, tree = _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD^{tree}")
    if not commit:
        return None, "not a git repository with a commit"
    if verdict not in ("pass", "fail"):
        return None, "verdict must be pass or fail"
    rec = {"commit": commit, "tree": tree, "verdict": verdict, "reviewer": reviewer, "findings": int(findings),
           "notes": notes[:2000], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _write(os.path.join(_reviews_dir(repo), commit + ".json"), rec)
    return rec, "ok"


def review_status(repo):
    commit, tree = _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD^{tree}")
    if not commit:
        return {"state": "none", "text": "No commit to review."}
    rec = _read(os.path.join(_reviews_dir(repo), commit + ".json"), None)
    dirty = bool(_git(repo, "status", "--porcelain", "--untracked-files=no"))
    if not rec or rec.get("tree") != tree:
        return {"state": "none", "commit": commit,
                "text": f"No independent review is recorded for commit {commit[:10]}."}
    note = " The working tree has uncommitted changes that the review did not see." if dirty else ""
    if rec["verdict"] == "pass":
        return {"state": "pass", "commit": commit,
                "text": f"Commit {commit[:10]} passed an independent review by {rec['reviewer']} at {rec['at']}.{note}"}
    return {"state": "fail", "commit": commit,
            "text": f"Commit {commit[:10]} failed its independent review ({rec['findings']} finding(s)) by {rec['reviewer']}.{note}"}


def _once(session, key):
    path = os.path.join(_sdir(session), "gates.json")
    fired = _read(path, {})
    if fired.get(key):
        return False
    fired[key] = time.time()
    _write(path, fired)
    return True


def stop_check(ev, cli="nodaris-harness"):
    """The reason to send the agent back once, or None."""
    if ev.get("stop_hook_active"):
        return None
    route = load_route(ev.get("session_id"))
    playbook, overlays = route.get("playbook"), set(route.get("overlays") or [])
    last = ev.get("last_assistant_message") or ""
    if playbook in ("investigate", "bug-fix") and last and not has_evidence(last):
        if _once(ev.get("session_id"), "evidence"):
            return ("This answer has no evidence in it. Before finishing, cite where the answer comes from: a file and "
                    "line (path/to/file.py:42), the command you ran with its exit code, or the decisive output quoted. "
                    "If something could not be checked, say so under Not verified.")
    if playbook in ("feature", "new-app") and overlays & SENSITIVE:
        records = design_records(ev.get("cwd") or ".", route.get("started", time.time()))
        good = [p for p in records if design_ok(p)[0] and design_approved(p)]
        if not good and _once(ev.get("session_id"), "design"):
            complete = [p for p in records if design_ok(p)[0]]
            if complete:
                why = (" The record is complete but has no approved review of its current content: have an independent "
                       "reviewer challenge it and record the verdict with `" + cli + " design review`.")
            else:
                why = f" The record found has a problem: {design_ok(records[-1])[1]}." if records else ""
            return (f"This work touches {', '.join(sorted(overlays & SENSITIVE))}, so it needs a design record before it "
                    f"is finished: docs/design/<date>-<topic>.md with the sections Options (at least two, each under a "
                    f"### heading), Chosen, Rejected and Acceptance checks. Follow the design skill.{why} Check it with "
                    f"`{cli} design check <file>`.")
    return None


PROTECTED_BRANCHES = {"main", "master", "prod", "production", "staging", "release"}


def branch_check(ev):
    """The first edit on a protected branch is sent back once with the branch instruction; the person can override."""
    path = (ev.get("tool_input") or {}).get("file_path") or (ev.get("tool_input") or {}).get("notebook_path") or ""
    where = os.path.dirname(os.path.join(ev.get("cwd") or ".", path)) if path else (ev.get("cwd") or ".")
    if not os.path.isdir(where):
        where = ev.get("cwd") or "."
    branch = _git(where, "rev-parse", "--abbrev-ref", "HEAD")
    if branch not in PROTECTED_BRANCHES:
        return None
    top = _git(where, "rev-parse", "--show-toplevel")
    if not _once(ev.get("session_id"), "branch:" + top):
        return None
    return (f"This repository is on `{branch}`. Changes go on their own branch: run `git switch -c feat/<short-topic>` "
            f"(or fix/, docs/, chore/) in {top}, then make the edit again. If the person asked to work on `{branch}` "
            f"directly, make the edit again and it will go through.")
