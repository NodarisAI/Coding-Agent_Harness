"""Team sync: share what the harness learned, never what it saw.

Opt-in only (settings.json `team_sync.enabled`, asked during onboarding). One run builds a bundle of:
- the person's own lessons (already redacted when they were written, redacted again here),
- the learner's change history (field, value, evidence count, when; never the prompts behind it),
- anonymous counts: which playbooks ran, which gates stopped work, which skills were used.

No code, prompts, file contents, file paths outside the harness, or patient data are included. The bundle is written
into a local clone of the team data repository and pushed to the person's own branch, `team/<handle>`. Every push
goes through the machine's normal git and push rules. Creating the team data repository is a maintainer's job;
this command never creates repositories.
"""
import collections, datetime, json, os, re, subprocess

from . import memory, policy, profile, redact, signals

HANDLE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,38}$")


def settings():
    try:
        with open(os.path.join(policy.home(), "settings.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _clean(text):
    r = redact.redact_text(str(text or "")[:2000])
    return r.text if r.verdict != "refused" else "[withheld]"


def bundle(since_days=30):
    since = (datetime.datetime.now() - datetime.timedelta(days=since_days)).timestamp()
    user_lib = memory.libraries(policy.home())[-1]
    lessons = []
    if os.path.exists(user_lib):
        for line in open(user_lib):
            try:
                l = json.loads(line)
            except ValueError:
                continue
            lessons.append({k: _clean(l.get(k)) for k in ("when", "do", "dont", "why")} |
                           {"id": l.get("id"), "keywords": l.get("keywords", [])[:12], "verified": bool(l.get("verified")),
                            "created": l.get("created")})
    changes = [{k: h.get(k) for k in ("id", "field", "value", "by", "at", "reverted")} |
               {"evidence_count": len(h.get("evidence") or []) if isinstance(h.get("evidence"), list) else h.get("evidence")}
               for h in profile.history()]
    counts = collections.Counter()
    for row in signals.load(since):
        kind = row.get("kind")
        key = row.get("playbook") or row.get("rule") or row.get("name") or ""
        counts[f"{kind}:{key}" if key else kind] += 1
    s = settings()
    return {"version": 1, "generated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "window_days": since_days,
            "packs": s.get("packs", []), "role": s.get("role"), "lessons": lessons, "learner_changes": changes,
            "counts": dict(sorted(counts.items()))}


def _git(cwd, *args, timeout=120):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def run(dry_run=False, since_days=30):
    """Returns (exit code, message)."""
    s = settings()
    team = s.get("team_sync") or {}
    if not team.get("enabled"):
        return 1, "Team sync is off. Turn it on with `nodaris-harness onboard --reconfigure` if your team uses it."
    repo, handle = str(team.get("repo") or ""), str(team.get("handle") or "").lower()
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo) or not HANDLE.match(handle):
        return 1, "Team sync needs a repository (owner/name) and a handle of lowercase letters, digits, dots or dashes."
    data = bundle(since_days)
    text = json.dumps(data, indent=2)
    if dry_run:
        return 0, text
    work = os.path.join(policy.home(), "team-data")
    branch = f"team/{handle}"
    if not os.path.isdir(os.path.join(work, ".git")):
        url = team.get("url") or f"https://github.com/{repo}.git"
        if subprocess.run(["git", "clone", "--quiet", url, work], capture_output=True, text=True, timeout=300).returncode:
            return 1, (f"Could not clone {repo}. Check that you have access to it (a maintainer creates it and adds "
                       f"you), then run this again.")
    _git(work, "fetch", "--quiet", "origin")
    if _git(work, "switch", "--quiet", branch)[0]:
        exists = _git(work, "rev-parse", "--verify", f"origin/{branch}")[0] == 0
        _git(work, "switch", "--quiet", "-c", branch, *( [f"origin/{branch}"] if exists else []))
    else:
        _git(work, "pull", "--quiet", "--ff-only", "origin", branch)
    folder = os.path.join(work, "members", handle)
    os.makedirs(folder, exist_ok=True)
    name = datetime.date.today().isoformat() + ".json"
    with open(os.path.join(folder, name), "w") as f:
        f.write(text + "\n")
    _git(work, "add", os.path.join("members", handle, name))
    code, out = _git(work, "commit", "--quiet", "-m", f"Harness learning from {handle}, {datetime.date.today().isoformat()}")
    if code and "nothing to commit" in out:
        return 0, "Nothing new to share since the last sync."
    code, out = _git(work, "push", "--quiet", "origin", branch, timeout=900)
    if code:
        return 1, f"The bundle is committed locally in {work} on {branch}, but the push did not go through: {out[-300:]}"
    return 0, f"Shared {len(data['lessons'])} lesson(s), {len(data['learner_changes'])} learner change(s) and the counts to {repo} on {branch}."
