#!/usr/bin/env python3
"""Stop hook: the model may not finish a coding turn without evidence.

Blocks when this session changed code and the checks below are not met. It blocks at most twice per turn and eight
times per session, and after a block it blocks again only once the agent has done something (an edit, a check, a
scan, a note), so an agent that has already been told is never sent round the same loop. The conditions:
  1. no test, lint, type or build command succeeded after the last code change; or
  2. a sensitive, non-test file changed and no test file with adversarial cases was written and then run
     successfully.
A session that changed no code is never blocked. The reason names the command to run.
Switch off with NODARIS_DONE_GATE=off.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hlib, repocard

MAX_BLOCKS = 2
MAX_SESSION_BLOCKS = 8
SCAN_TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "scan.py")
SCAN_TOOL = SCAN_TOOL if os.path.exists(SCAN_TOOL) else None


def sensitive_paths(state):
    last = {}
    for e in state.get("events", []):
        if e["kind"] == "edit" and e.get("sensitive"):
            last[e["path"]] = e["seq"]
    return last


def stem(path):
    return os.path.splitext(os.path.basename(path))[0].lower()


def unnamed_in_threat_model(state):
    """Sensitive files that no threat model note written this session names (all of them when there is no note)."""
    docs = [e for e in state.get("events", []) if e["kind"] == "doc" and e.get("threat")]
    if not docs:
        return sorted(sensitive_paths(state))
    text = " ".join(e.get("text", "") for e in docs)
    return sorted(p for p in sensitive_paths(state) if stem(p) not in text)


def unscanned(state):
    """Sensitive files with no clean scan that covered them after their last edit."""
    scans = [e for e in state.get("events", []) if e["kind"] == "scan" and e["ok"]]
    return sorted(p for p, seq in sensitive_paths(state).items()
                  if not any(s["seq"] > seq and p in s.get("files", []) for s in scans))


def verdict(state):
    ev = state.get("events", [])
    edits = [e for e in ev if e["kind"] == "edit"]
    if not edits:
        return []
    ok_checks = [e["seq"] for e in ev if e["kind"] == "check" and e["ok"]]
    last_edit = max(e["seq"] for e in edits)
    reasons = []
    if not any(s > last_edit for s in ok_checks):
        failed = [e for e in ev if e["kind"] == "check" and not e["ok"] and e["seq"] > last_edit]
        reasons.append("tests" if not failed else "failing")
    sensitive = [e for e in edits if e.get("sensitive")]
    if sensitive:
        attacks = [e["seq"] for e in edits if e.get("attack")]
        ran = [e["seq"] for e in ev if e["kind"] == "check" and e["ok"] and e.get("test") and e.get("ran", True)]
        if not attacks or not any(s > max(attacks) for s in ran):
            reasons.append("attack")
        if unnamed_in_threat_model(state):
            reasons.append("threat")
        if SCAN_TOOL and unscanned(state):
            reasons.append("scan")
    return reasons


def rel(path, card):
    return os.path.relpath(path, card["root"]) if card.get("root") and path.startswith(card["root"]) else path


def message(reasons, state, cwd):
    card = repocard.detect(cwd)
    cmd = ("`" + "`, `".join(card["verify"][:3]) + "`") if card["verify"] else "the repository's test command"
    parts = []
    if "tests" in reasons:
        parts.append(f"You changed code after the last successful check. Run the tests that cover the files you "
                     f"changed now and fix what fails; run the full check ({cmd}) once, as the last step before the summary.")
    if "failing" in reasons:
        parts.append(f"The last check after your change failed. Fix the cause (never skip or weaken a test) and rerun {cmd}.")
    if "attack" in reasons:
        files = sorted({os.path.relpath(e["path"], card["root"]) if e["path"].startswith(card["root"]) else e["path"]
                        for e in state["events"] if e["kind"] == "edit" and e.get("sensitive")})[:6]
        parts.append("You changed security-sensitive code (" + ", ".join(files) + ") without adversarial tests. "
                     "Follow the self-attack skill: try cross-tenant and unauthenticated access, injection, oversized and "
                     "malformed input, and PHI in logs, errors and responses against your change; write each applicable "
                     "attack as a test, run it, and report the attack table in your summary. Name each test after the "
                     "attack it performs (for example test_truncated_segment_is_rejected). Never rename or reword a "
                     "test only to satisfy this check: that is gaming the gate, and the receipt and the review show it. "
                     "If your tests already attack the change, say which ones in the summary.")
    if "threat" in reasons:
        missing = [rel(p, card) for p in unnamed_in_threat_model(state)][:6]
        parts.append("Write the threat model note for this change: docs/security/<feature>-threat-model.md (or the "
                     "repository's existing security or decision log). It must name each changed sensitive file ("
                     + ", ".join(missing) + ") and the endpoints they serve, every caller "
                     "kind, the trust boundary, and at least three abuse cases, each with the test that covers it. Check "
                     "the change against the rest of the security checklist while you write it: identity bound to the "
                     "principal, every layer enforcing, refusals audited, uniform errors, service callers still working.")
    if "scan" in reasons:
        missing = [rel(p, card) for p in unscanned(state)][:10]
        parts.append(f"Run the security scan over the sensitive files ({', '.join(missing)}): "
                     f"`python3 {SCAN_TOOL} --files {' '.join(missing)}`, without piping its output. Fix each finding, or suppress a proven "
                     "false positive inline with a one-line reason, and run it again until it is clean.")
    parts.append("Then finish with the summary: what changed, the commands you ran with their results, and what you could not verify.")
    return " ".join(parts)


def main():
    if os.environ.get("NODARIS_DONE_GATE", "").lower() == "off":
        return 0
    data = hlib.read_input()
    sid = data.get("session_id", "")
    with hlib.locked(sid) as state:
        if not data.get("stop_hook_active"):
            state["blocks"] = 0  # a fresh stop, not one the gate itself caused: the per-turn cap starts again
        reasons = verdict(state)
        ev = state.get("events", [])
        blocks = [e for e in ev if e["kind"] == "block"]
        if blocks and not any(e["seq"] > blocks[-1]["seq"] for e in ev if e["kind"] != "block"):
            return 0  # nothing done since the last block: repeating it would only cost another loop
        if not reasons or state.get("blocks", 0) >= MAX_BLOCKS or len(blocks) >= MAX_SESSION_BLOCKS:
            return 0
        state["blocks"] = state.get("blocks", 0) + 1
        hlib.add_event(state, kind="block", reasons=reasons)
        snapshot = json.loads(json.dumps(state))
    print(json.dumps({"decision": "block", "reason": message(reasons, snapshot, data.get("cwd") or os.getcwd())}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
