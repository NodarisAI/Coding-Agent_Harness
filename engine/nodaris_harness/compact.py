"""Compaction that keeps the asks: snapshot before the context is summarised, restore right after.

Before compaction the engine saves, from the session's redacted episode: every request the person made, verbatim
(patient identifiers already replaced), the files changed, the commands that failed and have not passed since, and
what the harness still requires. After compaction (SessionStart with source "compact") the snapshot comes back as
context, so a summary can never quietly drop an ask or a failure.
"""
import hashlib, json, os

from . import trajectory
from .policy import home

MAX_ASK = 1500


def _path(session_id):
    d = os.path.join(home(), "snapshots")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, hashlib.sha256(str(session_id).encode()).hexdigest()[:24] + ".json")


def snapshot(session_id):
    path = trajectory._path(session_id)
    steps = trajectory.load(path) if os.path.exists(path) else []
    asks = [s.get("prompt") for s in steps if s.get("event") == "UserPromptSubmit" and s.get("prompt")]
    changed, failing = [], {}
    for s in steps:
        inp = s.get("input") or {}
        if s.get("event") == "PreToolUse" and s.get("tool") in ("Write", "Edit") and inp.get("file_path") \
                and s["harness"]["decision"] != "deny" and inp["file_path"] not in changed:
            changed.append(inp["file_path"])
        if s.get("event") in ("PostToolUse", "PostToolUseFailure") and s.get("tool") == "Bash" and inp.get("command"):
            key = inp["command"][:200]
            if s.get("ok") is False:
                failing[key] = (s.get("output") or "")[-300:]
            else:
                failing.pop(key, None)
    blocks = [s["harness"]["reason"] for s in steps if s.get("event") == "Stop" and s["harness"]["decision"] == "block"]
    snap = {"asks": asks, "changed": changed[-40:], "failing": failing, "last_gate": blocks[-1] if blocks else None}
    with open(_path(session_id), "w") as fh:
        json.dump(snap, fh)
    return snap


def restore(session_id):
    path = _path(session_id)
    if not os.path.exists(path):
        return ""
    snap = json.load(open(path))
    out = ["Working state restored after compaction. The requests below are the person's own words and win over "
           "any summary."]
    if snap["asks"]:
        out.append("Requests, oldest first:")
        out += [f"{i}. {a[:MAX_ASK]}" for i, a in enumerate(snap["asks"], 1)]
    if snap["changed"]:
        out.append("Files changed so far: " + ", ".join(snap["changed"]))
    if snap["failing"]:
        out.append("Commands that failed and have not passed since:")
        out += [f"- {cmd}\n  last output: {tail}" for cmd, tail in snap["failing"].items()]
    if snap.get("last_gate"):
        out.append("The done gate last required: " + snap["last_gate"])
    return "\n".join(out)
