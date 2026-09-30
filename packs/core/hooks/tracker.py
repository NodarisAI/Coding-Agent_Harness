#!/usr/bin/env python3
"""PostToolUse and PostToolUseFailure hook: records code edits and verify commands for the done gate.

Edits: the path, whether it is code, a test, or sensitive, and whether a test file carries adversarial cases.
Commands: only commands that look like a test, lint, type or build run, with whether they succeeded. A Bash
call counts as failed when the event is PostToolUseFailure or the response reports a non-zero exit code.
Never blocks, never prints.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hlib

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def bash_ok(data):
    """PostToolUseFailure carries non-zero exits ("Exit code N"); PostToolUse carries successes with stdout. A
    success whose output shows failing tests (hidden by a pipe) is a failure; an unrecognised shape fails closed."""
    if data.get("hook_event_name") != "PostToolUse":
        return False
    resp = data.get("tool_response")
    if not isinstance(resp, dict):
        return False
    if resp.get("interrupted") is True:
        return False
    return hlib.output_verdict(resp.get("stdout", "")) != "fail"


def main():
    data = hlib.read_input()
    tool = data.get("tool_name", "")
    sid = data.get("session_id", "")
    ti = data.get("tool_input") or {}
    if tool in EDIT_TOOLS:
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if data.get("hook_event_name") == "PostToolUseFailure":
            return
        if path.lower().endswith(hlib.DOC_EXT):
            # Only what this write added counts: editing a document that already mentions a threat model is not
            # writing one. The text is kept so the gate can check that it names the changed files.
            body = hlib.written_text(ti)
            if hlib.THREAT_TEXT.search(body):
                with hlib.locked(sid) as state:
                    hlib.add_event(state, kind="doc", path=path, threat=True, text=body[:20000].lower())
            return
        if not hlib.is_code(path):
            return
        text = hlib.written_text(ti)
        test = hlib.is_test(path)
        with hlib.locked(sid) as state:
            hlib.add_event(state, kind="edit", path=path, test=test,
                           sensitive=(not test) and hlib.is_sensitive(path, text),
                           attack=test and bool(hlib.ATTACK_TEXT.search(text)) and bool(hlib.ASSERTS.search(text)))
    elif tool in ("Task", "Agent"):
        kind = (ti.get("subagent_type") or "").strip()
        if not kind:
            return
        with hlib.locked(sid) as state:
            hlib.add_event(state, kind="agent", agent=kind[:60], ok=data.get("hook_event_name") == "PostToolUse")
    elif tool == "Bash":
        cmd = ti.get("command") or ""
        if hlib.SCAN_CMD.search(cmd):
            resp = data.get("tool_response") if isinstance(data.get("tool_response"), dict) else {}
            out = str(resp.get("stdout") or "")
            files = []
            for line in out.splitlines():
                if line.startswith("scan: scanned files: "):
                    files = [f.strip() for f in line[len("scan: scanned files: "):].split(",") if f.strip()]
            root = hlib.git_root(data.get("cwd") or "")
            # Clean means the command succeeded and the tool itself said so; a pipe can hide a non-zero exit.
            ok = data.get("hook_event_name") == "PostToolUse" and "scan: clean" in out
            with hlib.locked(sid) as state:
                hlib.add_event(state, kind="scan", ok=ok,
                               files=[os.path.join(root, f) if root else f for f in files][:300])
            return
        if not hlib.verify_segments(cmd):
            return
        ok = bash_ok(data)
        stdout = (data.get("tool_response") or {}).get("stdout", "") if isinstance(data.get("tool_response"), dict) else ""
        with hlib.locked(sid) as state:
            hlib.add_event(state, kind="check", cmd=cmd[:200], ok=ok, test=hlib.is_test_run(cmd),
                           ran=ok and hlib.output_verdict(stdout) == "pass")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
