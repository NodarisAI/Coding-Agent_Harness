"""Team sync: share what the harness learned with the Nodaris memory vault, never what it saw.

Opt-in only (settings.json `team_sync.enabled`, asked once during onboarding). Once a day, at the start of a session,
the harness runs this in the background; `nodaris-harness sync` runs it by hand and `--dry-run` shows what would go.

What goes, into the vault repository (NodarisAI/Nodaris-Memory-Vault) on the person's own branch
`agent/team-memory/<handle>`:
- `team/<handle>/lessons/<id>.md`: each of the person's own lessons as a page (redacted when written, again here),
- `team/<handle>/telemetry/<date>.json`: the learner's change history (never the prompts behind it) and anonymous
  counts: playbooks run, gates that stopped work, skills used, subagent tokens spent.

No code, prompts, replies, file contents, file paths outside the harness, secrets or patient data are included. A
maintainer merges the members' branches into the vault's `team/` folder with `nodaris-harness team-intake`, and every
sync reads the merged team lessons back, so a lesson one person learned is recalled for everyone.
"""
import collections, datetime, glob, json, os, re, subprocess, sys, time

from . import __version__, memory, policy, profile, redact, signals

VAULT = "NodarisAI/Nodaris-Memory-Vault"
HANDLE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,38}$")   # onboarding validates handles with this same pattern
URL = re.compile(r"^(https://[\w.-]+/[\w.-]+/[\w.-]+?(\.git)?|/[\w./-]+)$")
BRANCH = "agent/team-memory/{handle}"
EVERY_HOURS = 20
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")


def settings():
    try:
        with open(os.path.join(policy.home(), "settings.json")) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clean(text):
    r = redact.redact_text(str(text or "")[:2000])
    return r.text if r.verdict != "refused" else "[withheld]"


def _lessons():
    user_lib = memory.libraries(policy.home())[-1]
    out = []
    if os.path.exists(user_lib):
        for line in open(user_lib):
            try:
                l = json.loads(line)
            except ValueError:
                continue
            if not re.fullmatch(r"[0-9a-f]{6,40}", str(l.get("id") or "")):
                continue
            out.append({"id": l["id"], **{k: _clean(l.get(k)) for k in ("when", "do", "dont", "why")},
                        "keywords": [_clean(k)[:60] for k in (l.get("keywords") or [])[:12]],
                        "verified": bool(l.get("verified")), "created": str(l.get("created") or "")[:10]})
    return out


def _subagent_tokens(since):
    total, sessions = 0, 0
    for path in glob.glob(os.path.join(policy.home(), "sessions", "*", "subagents.json")):
        try:
            if os.path.getmtime(path) < since:
                continue
            spent = int((json.load(open(path)) or {}).get("spent") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        total += spent
        sessions += 1
    return {"subagent_tokens": total, "sessions_with_subagents": sessions}


def bundle(since_days=30):
    since = (datetime.datetime.now() - datetime.timedelta(days=since_days)).timestamp()
    changes = [{k: h.get(k) for k in ("id", "field", "value", "by", "at", "reverted")} |
               {"evidence_count": len(h.get("evidence") or []) if isinstance(h.get("evidence"), list) else h.get("evidence")}
               for h in profile.history()]
    counts = collections.Counter()
    for row in signals.load(since):
        kind = row.get("kind")
        key = row.get("playbook") or row.get("rule") or row.get("name") or ""
        counts[f"{kind}:{key}" if key else kind] += 1
    s = settings()
    return {"version": 2, "harness": __version__, "generated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "window_days": since_days, "packs": s.get("packs", []), "role": s.get("role"), "hosts": s.get("hosts", []),
            "lessons": _lessons(), "learner_changes": changes, "counts": dict(sorted(counts.items())),
            "tokens": _subagent_tokens(since)}


def lesson_page(lesson, handle):
    """One lesson as a vault page: front matter the vault's tools can index, then the instruction in plain words."""
    kw = ", ".join(json.dumps(k) for k in lesson.get("keywords") or [])
    lines = ["---", f"id: {lesson['id']}", "type: lesson", "source: nodaris-harness", f"member: {handle}",
             f"keywords: [{kw}]", f"verified: {'true' if lesson.get('verified') else 'false'}",
             f"created: {lesson.get('created') or ''}", "---", "", f"**When:** {lesson.get('when') or ''}", "",
             f"**Do:** {lesson.get('do') or ''}"]
    if lesson.get("dont"):
        lines += ["", f"**Don't:** {lesson['dont']}"]
    if lesson.get("why"):
        lines += ["", f"**Why:** {lesson['why']}"]
    return "\n".join(lines) + "\n"


def parse_page(text):
    """The reverse of lesson_page, for recall. Returns a lesson dict or None."""
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        return None
    meta = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))
    if not re.fullmatch(r"[0-9a-f]{6,40}", meta.get("id", "")):
        return None
    body = dict(re.findall(r"\*\*(When|Do|Don't|Why):\*\*\s*(.*)", m.group(2)))
    try:
        keywords = json.loads(meta.get("keywords") or "[]")
    except ValueError:
        keywords = []
    return {"id": meta["id"], "when": body.get("When", ""), "do": body.get("Do", ""), "dont": body.get("Don't", ""),
            "why": body.get("Why", ""), "keywords": [str(k).lower() for k in keywords if isinstance(k, str)],
            "files": [], "verified": meta.get("verified") == "true", "created": meta.get("created", ""),
            "member": meta.get("member", ""), "team": True}


def _git(cwd, *args, timeout=120):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout, env=GIT_ENV)
    except subprocess.TimeoutExpired:
        return 1, f"git {args[0]} took longer than {timeout} seconds"
    return r.returncode, (r.stdout + r.stderr).strip()


def _target():
    s = settings()
    team = s.get("team_sync") or {}
    repo = str(team.get("repo") or VAULT)
    handle = str(team.get("handle") or "").lower()
    return team, repo, handle


def _stamp():
    return os.path.join(policy.home(), "state", "last-team-sync")


def refresh_team_library(work, handle):
    """Write the team's merged lessons (everyone but this person) to the local team library used by recall."""
    code, out = _git(work, "ls-tree", "-r", "--name-only", "origin/main", "--", "team")
    if code:
        return 0
    lessons = []
    for path in out.splitlines():
        parts = path.split("/")
        if len(parts) != 4 or parts[2] != "lessons" or not path.endswith(".md") or parts[1] == handle:
            continue
        code, text = _git(work, "show", f"origin/main:{path}")
        lesson = parse_page(text + "\n") if not code else None
        if lesson:
            lessons.append(lesson)
    target = memory.team_library()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w") as fh:
        for l in lessons:
            fh.write(json.dumps(l) + "\n")
    os.replace(tmp, target)
    return len(lessons)


def run(dry_run=False, since_days=30):
    """Returns (exit code, message)."""
    team, repo, handle = _target()
    if not team.get("enabled"):
        return 1, "Team sync is off. Turn it on with `nodaris-harness onboard --reconfigure` if your team uses it."
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo) or not HANDLE.match(handle):
        return 1, "Team sync needs a repository (owner/name) and a handle of lowercase letters, digits, dots or dashes."
    data = bundle(since_days)
    if dry_run:
        return 0, json.dumps(data, indent=2)
    work = os.path.join(policy.home(), "team-data")
    branch = BRANCH.format(handle=handle)
    if not os.path.isdir(os.path.join(work, ".git")):
        url = str(team.get("url") or f"https://github.com/{repo}.git")
        if not URL.match(url):
            return 1, "The team repository address must be an https:// repository address."
        try:
            failed = subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", "--", url, work],
                                    capture_output=True, text=True, timeout=600, env=GIT_ENV).returncode
        except subprocess.TimeoutExpired:
            failed = True
        if failed:
            return 1, (f"Could not reach {repo}. Check that your GitHub account can open it, then run "
                       f"`nodaris-harness sync` again.")
        _git(work, "sparse-checkout", "set", "--no-cone", f"/team/{handle}/")
    if _git(work, "fetch", "--quiet", "origin")[0]:
        return 1, f"Could not fetch {repo}. Check your network and GitHub access."
    if _git(work, "switch", "--quiet", branch)[0]:
        exists = _git(work, "rev-parse", "--verify", f"origin/{branch}")[0] == 0
        base = f"origin/{branch}" if exists else ("origin/main" if _git(work, "rev-parse", "--verify", "origin/main")[0] == 0 else None)
        _git(work, "switch", "--quiet", "-c", branch, *([base] if base else []))
    elif _git(work, "rev-parse", "--verify", f"origin/{branch}")[0] == 0:
        _git(work, "merge", "--quiet", "--ff-only", f"origin/{branch}")
    mine = os.path.join(work, "team", handle)
    lesson_dir, tele_dir = os.path.join(mine, "lessons"), os.path.join(mine, "telemetry")
    os.makedirs(lesson_dir, exist_ok=True)
    os.makedirs(tele_dir, exist_ok=True)
    keep = set()
    for lesson in data["lessons"]:
        name = f"{lesson['id']}.md"
        keep.add(name)
        with open(os.path.join(lesson_dir, name), "w") as f:
            f.write(lesson_page(lesson, handle))
    for old in os.listdir(lesson_dir):
        if old.endswith(".md") and old not in keep:
            os.remove(os.path.join(lesson_dir, old))
    # The file name carries the date; leaving the time out means an unchanged day commits nothing.
    telemetry = {k: v for k, v in data.items() if k not in ("lessons", "generated")} | {"lesson_count": len(data["lessons"])}
    with open(os.path.join(tele_dir, datetime.date.today().isoformat() + ".json"), "w") as f:
        f.write(json.dumps(telemetry, indent=2) + "\n")
    _git(work, "add", "-A", "--", os.path.join("team", handle))
    code, out = _git(work, "commit", "--quiet", "-m", f"Team memory from {handle}, {datetime.date.today().isoformat()}")
    shared = code == 0
    if shared:
        code, out = _git(work, "push", "--quiet", "origin", branch, timeout=900)
        if code:
            return 1, f"The update is committed locally in {work} on {branch}, but the push did not go through: {out[-300:]}"
    elif "nothing to commit" not in out:
        return 1, f"Could not commit the update: {out[-300:]}"
    team_count = refresh_team_library(work, handle)
    os.makedirs(os.path.dirname(_stamp()), exist_ok=True)
    open(_stamp(), "w").write(str(time.time()))
    head = (f"Shared {len(data['lessons'])} lesson(s) and today's usage counts with {repo} on {branch}." if shared
            else "Nothing new to share since the last sync.")
    return 0, f"{head} {team_count} lesson(s) from the rest of the team are now in your recall."


def due():
    team, _, _ = _target()
    if not team.get("enabled"):
        return False
    try:
        return time.time() - float(open(_stamp()).read().strip() or 0) > EVERY_HOURS * 3600
    except (OSError, ValueError):
        return True


def start_in_background(cli):
    """Called at session start: share and refresh team memory once a day, detached, when the person opted in."""
    if os.environ.get("NODARIS_HARNESS_NO_BG") or not due():
        return False
    try:
        os.makedirs(os.path.dirname(_stamp()), exist_ok=True)
        open(_stamp(), "w").write(str(time.time()))   # one attempt per day, even if this one fails
        subprocess.Popen([cli, "sync", "--quiet"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


# ---- maintainers: merge the members' branches into the vault ----------------------------------------------------

PAGE_RE = re.compile(r"^team/([a-z0-9][a-z0-9._-]{0,38})/(lessons/[0-9a-f]{6,40}\.md|telemetry/\d{4}-\d{2}-\d{2}\.json)$")


def intake(vault, dry_run=False, bump=True, today=None):
    """Bring every `agent/team-memory/<handle>` branch's `team/<handle>/` folder into a review branch of the vault.
    Only well-formed lesson pages and telemetry files are taken, each re-checked by redaction. Returns (code, text)."""
    today = today or datetime.date.today().isoformat()
    if not os.path.isdir(os.path.join(vault, ".git")):
        return 1, f"{vault} is not a clone of the memory vault."
    if _git(vault, "fetch", "--quiet", "origin")[0]:
        return 1, "Could not fetch the vault."
    code, out = _git(vault, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/agent/team-memory/")
    branches = [b for b in out.splitlines() if b.strip()]
    taken, refused = collections.defaultdict(list), []
    for ref in branches:
        handle = ref.rsplit("/", 1)[-1]
        if not HANDLE.match(handle):
            refused.append(f"{ref}: not a valid handle")
            continue
        code, names = _git(vault, "diff", "--name-only", "origin/main", ref, "--", f"team/{handle}")
        for path in names.splitlines():
            m = PAGE_RE.match(path)
            if not m or m.group(1) != handle:
                refused.append(f"{path}: outside team/{handle}/ or not a lesson or telemetry file")
                continue
            code, text = _git(vault, "show", f"{ref}:{path}")
            if code:
                taken[handle].append((path, None))   # deleted on the member's branch
                continue
            if redact.redact_text(text).text != text:
                refused.append(f"{path}: contains something redaction would change")
                continue
            taken[handle].append((path, text))
    summary = [f"{h}: {len(v)} file(s)" for h, v in sorted(taken.items())]
    if dry_run or not taken:
        return 0, ("Would take " if dry_run else "Nothing to take. ") + "; ".join(summary) + \
            ("" if not refused else " Refused: " + "; ".join(refused[:10]))
    branch = f"chore/team-memory-{today}"
    if _git(vault, "switch", "--quiet", "-c", branch, "origin/main")[0]:
        return 1, f"Could not create {branch}; it may exist already."
    for handle, files in taken.items():
        for path, text in files:
            full = os.path.join(vault, path)
            if text is None:
                if os.path.exists(full):
                    os.remove(full)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text if text.endswith("\n") else text + "\n")
    _write_index(vault, today)
    if bump:
        _bump_plugin(vault)
    _git(vault, "add", "-A", "--", "team", ".claude-plugin")
    _git(vault, "commit", "--quiet", "-m", f"Team memory intake {today}: " + ", ".join(summary))
    return 0, (f"Committed on {branch}: " + "; ".join(summary) + ". Review it, then push the branch and open a pull "
               "request." + ("" if not refused else " Refused: " + "; ".join(refused[:10])))


def _write_index(vault, today):
    rows, members = [], collections.Counter()
    for path in sorted(glob.glob(os.path.join(vault, "team", "*", "lessons", "*.md"))):
        lesson = parse_page(open(path).read())
        if lesson:
            members[lesson["member"]] += 1
            rel = os.path.relpath(path, os.path.join(vault, "team"))
            rows.append(f"| [{lesson['when'][:80] or lesson['id']}]({rel}) | {lesson['member']} | "
                        f"{', '.join(lesson['keywords'][:5])} | {'yes' if lesson['verified'] else 'no'} |")
    text = ["# Team memory", "", f"Lessons the harness learned on each team member's machine, redacted before they left "
            f"it and merged here by `nodaris-harness team-intake`. Updated {today}.", "",
            "Members: " + (", ".join(f"{m} ({n})" for m, n in sorted(members.items())) or "none yet"), "",
            "| Lesson | Member | Keywords | Verified |", "|---|---|---|---|"] + rows
    with open(os.path.join(vault, "team", "INDEX.md"), "w") as fh:
        fh.write("\n".join(text) + "\n")


def _bump_plugin(vault):
    paths = [os.path.join(vault, ".claude-plugin", n) for n in ("plugin.json", "marketplace.json")]
    try:
        plugin = json.load(open(paths[0]))
        market = json.load(open(paths[1]))
    except (OSError, ValueError):
        return None
    major, minor, patch = (int(x) for x in str(plugin["version"]).split("."))
    plugin["version"] = f"{major}.{minor}.{patch + 1}"
    for entry in market.get("plugins", []):
        if entry.get("name") == plugin.get("name"):
            entry["version"] = plugin["version"]
    for path, data in zip(paths, (plugin, market)):
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
    return plugin["version"]
