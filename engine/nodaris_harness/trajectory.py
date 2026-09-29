"""Episode recorder and dataset exporters.

Every hook event of a session is appended to <harness home>/episodes/<session>.jsonl after redaction, so an episode
never holds patient information in the clear: prompts, commands, file text and tool output pass through surrogate
redaction first, and the redaction verdict is stored with each step. Text that cannot be scanned is dropped and the
step says so.

`export` turns episodes into two JSON Lines datasets (schema in harness/docs/TRAJECTORY-SCHEMA.md):
- sft: one record per episode with the request, the ordered tool steps, the harness interventions and the outcome;
- eval: one record per episode with the request, what the harness required, and whether the session met it.
"""
import glob, hashlib, json, os, time

from . import redact
from .policy import home

TEXT_LIMIT = 20_000
OUTPUT_LIMIT = 4_000
SCHEMA_VERSION = "1"


def episodes_dir():
    d = os.path.join(home(), "episodes")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _path(session_id):
    safe = hashlib.sha256(str(session_id).encode()).hexdigest()[:24]
    return os.path.join(episodes_dir(), safe + ".jsonl")


def _clean(value, limit, sur, verdicts):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text[:limit]
    r = redact.redact_text(text, sur)
    verdicts.append(r.verdict)
    return r.text if r.verdict != "refused" else "[dropped: could not be scanned]"


def record(ev, outcome, extra=None):
    """Append one redacted step. Never raises: recording must not break the agent's work."""
    try:
        sur, verdicts = redact.Surrogates(), []
        ti = ev.get("tool_input") or {}
        step = {"v": SCHEMA_VERSION, "ts": round(time.time(), 3), "host": ev.get("host"), "event": ev.get("hook_event_name"),
                "tool": ev.get("tool_name") or None, "cwd_hash": hashlib.sha256(str(ev.get("cwd")).encode()).hexdigest()[:12]}
        if ev.get("hook_event_name") == "UserPromptSubmit":
            step["prompt"] = _clean(ev.get("prompt") or "", TEXT_LIMIT, sur, verdicts)
        if ti:
            step["input"] = {k: _clean(ti.get(k), TEXT_LIMIT, sur, verdicts) for k in ("command", "file_path", "content",
                                                                                       "new_string", "old_string") if ti.get(k)}
        if ev.get("hook_event_name") in ("PostToolUse", "PostToolUseFailure"):
            step["ok"] = ev.get("hook_event_name") == "PostToolUse"
            result = ev.get("tool_response") if ev.get("tool_response") is not None else ev.get("error")
            if result is not None:
                step["output"] = _clean(result, OUTPUT_LIMIT, sur, verdicts)
        if ev.get("hook_event_name") == "Stop" and ev.get("last_assistant_message"):
            step["final_message"] = _clean(ev["last_assistant_message"], TEXT_LIMIT, sur, verdicts)
        step["harness"] = {"decision": outcome.get("decision", "allow"), "rule": outcome.get("rule") or None,
                           "reason": _clean(outcome.get("reason"), 2000, sur, verdicts) if outcome.get("reason") else None,
                           "context_chars": len(outcome.get("context") or "")}
        if outcome.get("route"):
            step["route"] = outcome["route"]
        if extra:
            step.update(extra)
        step["redaction"] = "refused" if "refused" in verdicts else "redacted" if "redacted" in verdicts else "clean"
        sur.burn()
        with open(_path(ev.get("session_id")), "a") as fh:
            fh.write(json.dumps(step) + "\n")
    except Exception:  # noqa: BLE001 - the recorder is best effort by design
        pass


def load(path):
    steps = []
    with open(path) as fh:
        for line in fh:
            try:
                steps.append(json.loads(line))
            except ValueError:
                continue
    return steps


def _episode_id(path):
    return os.path.splitext(os.path.basename(path))[0]


def _outcome(steps):
    stops = [s for s in steps if s.get("event") == "Stop"]
    blocks = [s for s in stops if s["harness"]["decision"] == "block"]
    last = stops[-1] if stops else None
    return {"stop_attempts": len(stops), "stop_blocks": len(blocks),
            "finished_clean": bool(last and last["harness"]["decision"] != "block"),
            "refused_calls": sum(1 for s in steps if s["harness"]["decision"] == "deny"),
            "failed_calls": sum(1 for s in steps if s.get("ok") is False)}


def to_sft(path):
    steps = load(path)
    messages = []
    for s in steps:
        if s.get("event") == "UserPromptSubmit":
            messages.append({"role": "user", "content": s.get("prompt") or ""})
        elif s.get("event") == "PreToolUse":
            messages.append({"role": "assistant", "tool_call": {"tool": s.get("tool"), "input": s.get("input") or {}}})
            if s["harness"]["decision"] == "deny":
                messages.append({"role": "harness", "decision": "deny", "rule": s["harness"]["rule"],
                                 "content": s["harness"]["reason"]})
        elif s.get("event") in ("PostToolUse", "PostToolUseFailure"):
            messages.append({"role": "tool", "tool": s.get("tool"), "ok": s.get("ok"), "content": s.get("output")})
            if s["harness"]["context_chars"]:
                messages.append({"role": "harness", "decision": "context", "content": None})
        elif s.get("event") == "Stop":
            if s.get("final_message"):
                messages.append({"role": "assistant", "content": s["final_message"]})
            if s["harness"]["decision"] == "block":
                messages.append({"role": "harness", "decision": "block", "rule": "done-gate", "content": s["harness"]["reason"]})
    return {"schema": "nodaris-harness/sft", "version": SCHEMA_VERSION, "id": _episode_id(path),
            "host": next((s.get("host") for s in steps if s.get("host")), None), "messages": messages,
            "outcome": _outcome(steps), "redaction": sorted({s.get("redaction") for s in steps})}


def to_eval(path):
    steps = load(path)
    prompts = [s.get("prompt") for s in steps if s.get("event") == "UserPromptSubmit"]
    required = [{"rule": s["harness"]["rule"] or ("done-gate" if s.get("event") == "Stop" else None),
                 "reason": s["harness"]["reason"]} for s in steps if s["harness"]["decision"] in ("deny", "block")]
    return {"schema": "nodaris-harness/eval", "version": SCHEMA_VERSION, "id": _episode_id(path),
            "task": prompts[0] if prompts else None, "follow_ups": prompts[1:], "harness_required": required,
            "outcome": _outcome(steps), "tools_used": sorted({s.get("tool") for s in steps if s.get("tool")})}


def export(fmt, out_path, directory=None):
    """Write every episode as one JSON line. Returns the number of records written."""
    conv = {"sft": to_sft, "eval": to_eval}[fmt]
    files = sorted(glob.glob(os.path.join(directory or episodes_dir(), "*.jsonl")))
    n = 0
    with open(out_path, "w") as fh:
        for f in files:
            rec = conv(f)
            if fmt == "sft" and not rec["messages"]:
                continue
            fh.write(json.dumps(rec) + "\n")
            n += 1
    return n
