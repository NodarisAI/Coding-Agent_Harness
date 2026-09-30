"""Are we done? Run an acceptance list and say, item by item, what passes, what fails and what waits on a person.

An acceptance list is a JSON file (by default `.nodaris/acceptance.json` in the repository) of checks:

  {"name": "Version 1", "checks": [
     {"id": "tests", "title": "The test suite passes", "run": ["python3", "-m", "pytest", "-q"], "timeout": 900},
     {"id": "docs", "title": "The install guide exists", "files": ["INSTALL.md"]},
     {"id": "prompt", "title": "The README has the setup prompt", "contains": {"README.md": ["Install the Nodaris harness"]}},
     {"id": "marks", "title": "No AI authorship marks", "absent": ["Co-Authored-By: Claude"]},
     {"id": "guards", "title": "Guards installed", "owner": "Varun", "action": "Run the guard installer.", "run": [...]}
  ]}

A check passes when every test it names passes: `run` exits 0 (in the repository, with `env` added), every path in
`files` exists, every string in `contains` is in its file, and no string in `absent` is in a tracked file. A failing
check with an `owner` is reported as waiting on that person, with its `action`, instead of as a failure; the agent
cannot close it. `--arm` makes the Stop hook ask the same question before the session may finish, so the agent keeps
working until the list passes or only a person's steps remain.
"""
import json, os, subprocess, time

from . import gates

DEFAULT = os.path.join(".nodaris", "acceptance.json")
MAX_BLOCKS = 6


class ManifestError(ValueError):
    pass


def load(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except OSError:
        raise ManifestError(f"There is no acceptance list at {path}.")
    except ValueError as e:
        raise ManifestError(f"{path} is not valid JSON: {e}")
    checks = data.get("checks") if isinstance(data, dict) else None
    if not isinstance(checks, list) or not checks:
        raise ManifestError(f"{path} needs a non-empty \"checks\" list.")
    seen = set()
    for i, c in enumerate(checks):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(c.get("title"), str):
            raise ManifestError(f"Check {i + 1} needs an id and a title.")
        if c["id"] in seen:
            raise ManifestError(f"The id {c['id']} is used twice.")
        seen.add(c["id"])
        if not any(k in c for k in ("run", "files", "contains", "absent")):
            raise ManifestError(f"Check {c['id']} names no test (run, files, contains or absent).")
        if "run" in c and not (isinstance(c["run"], list) and c["run"] and all(isinstance(x, str) for x in c["run"])):
            raise ManifestError(f"Check {c['id']}: run must be a list of arguments, not a shell string.")
    return data


def _tail(text, n=400):
    text = (text or "").strip()
    return text if len(text) <= n else "..." + text[-n:]


def _tracked_hits(root, needle):
    try:
        r = subprocess.run(["git", "-C", root, "grep", "-l", "-F", "-e", needle, "--", ".",
                            ":(exclude).nodaris/acceptance.json"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ["(git grep could not run)"]
    return [l for l in r.stdout.splitlines() if l.strip()]


def run_check(check, root):
    """Returns (passed, detail)."""
    for rel in check.get("files", []):
        if not os.path.exists(os.path.join(root, rel)):
            return False, f"{rel} is missing."
    for rel, needles in (check.get("contains") or {}).items():
        try:
            text = open(os.path.join(root, rel), errors="ignore").read()
        except OSError:
            return False, f"{rel} is missing."
        for n in needles:
            if n not in text:
                return False, f"{rel} does not contain \"{n}\"."
    for needle in check.get("absent", []):
        hits = _tracked_hits(root, needle)
        if hits:
            return False, f"\"{needle}\" appears in {', '.join(hits[:5])}."
    if "run" in check:
        env = dict(os.environ, **{k: str(v) for k, v in (check.get("env") or {}).items()})
        try:
            r = subprocess.run(check["run"], cwd=root, env=env, capture_output=True, text=True,
                               timeout=int(check.get("timeout", 300)))
        except subprocess.TimeoutExpired:
            return False, f"It took longer than {check.get('timeout', 300)} seconds."
        except OSError as e:
            return False, f"It could not start: {e.strerror or e}."
        if r.returncode != 0:
            return False, f"Exit code {r.returncode}. {_tail(r.stdout + r.stderr)}"
    return True, ""


def evaluate(path, root=None, only=None):
    data = load(path)
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(path)))
    results = []
    for c in data["checks"]:
        if only and c["id"] not in only:
            continue
        started = time.time()
        ok, detail = run_check(c, root)
        status = "pass" if ok else ("waiting" if c.get("owner") else "fail")
        results.append({"id": c["id"], "title": c["title"], "status": status, "detail": detail,
                        "owner": c.get("owner"), "action": c.get("action"), "seconds": round(time.time() - started, 1)})
    return data.get("name") or "Acceptance list", results


def verdict(results):
    failed = [r for r in results if r["status"] == "fail"]
    waiting = [r for r in results if r["status"] == "waiting"]
    if failed:
        return 1, f"Not done: {len(failed)} check(s) fail" + (f" and {len(waiting)} wait on a person." if waiting else ".")
    if waiting:
        return 2, (f"Done except for {len(waiting)} step(s) only a person can take: "
                   + "; ".join(f"{r['owner']}: {r['action'] or r['title']}" for r in waiting) + ".")
    return 0, "Done: every check passes."


def report(name, results, stream=None):
    import sys
    out = stream or sys.stdout
    marks = {"pass": "PASS", "fail": "FAIL", "waiting": "WAIT"}
    out.write(f"{name}\n")
    for r in results:
        out.write(f"  {marks[r['status']]}  {r['title']}  ({r['seconds']}s)\n")
        if r["status"] == "fail":
            out.write(f"        {r['detail']}\n")
        elif r["status"] == "waiting":
            out.write(f"        Waiting on {r['owner']}: {r['action'] or r['detail']}\n")
    code, line = verdict(results)
    out.write(f"\n{line}\n")
    return code


# ---- the loop: the Stop hook asks the same question ---------------------------------------------------------------

def _armed_path(session):
    return os.path.join(gates._sdir(session), "ready.json")


def arm(session, manifest):
    gates._write(_armed_path(session), {"manifest": os.path.abspath(manifest), "blocks": 0})


def disarm(session):
    try:
        os.remove(_armed_path(session))
    except OSError:
        pass


def stop_check(session, cli="nodaris-harness"):
    """Reason to keep working, or None. Only while armed; gives up after MAX_BLOCKS so a session can never be stuck."""
    state = gates._read(_armed_path(session), None)
    if not isinstance(state, dict) or not state.get("manifest"):
        return None
    try:
        name, results = evaluate(state["manifest"])
    except ManifestError:
        disarm(session)
        return None
    code, line = verdict(results)
    if code != 1:
        disarm(session)
        return None
    state["blocks"] = int(state.get("blocks") or 0) + 1
    if state["blocks"] > MAX_BLOCKS:
        disarm(session)
        return None
    gates._write(_armed_path(session), state)
    failing = [r for r in results if r["status"] == "fail"]
    lines = [f"{name}: {line} Round {state['blocks']} of {MAX_BLOCKS}. Keep working on these before you finish:"]
    lines += [f"- {r['title']}: {r['detail']}" for r in failing[:8]]
    lines.append(f"Run `{cli} ready` to check again. Steps that wait on a person do not stop you; list them in your reply.")
    return "\n".join(lines)
