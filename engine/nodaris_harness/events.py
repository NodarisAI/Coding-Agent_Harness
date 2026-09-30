"""Host adapters: turn each agent's hook payload into one canonical event, and the engine's outcome back into what
that agent understands. The canonical event is Claude Code shaped, because the v2 pipeline was written for it.

Contracts and their sources are in harness/hosts/CONTRACTS.md. Where a contract is marked unverified there, the
adapter uses the documented shape and the host is labelled "contract-tested" rather than "live-verified".

Canonical event keys: hook_event_name (PreToolUse, PostToolUse, PostToolUseFailure, UserPromptSubmit, Stop,
PreCompact, SessionStart), tool_name (Bash, Read, Write, Edit or the host's own name), tool_input (command, file_path,
content, new_string), tool_response, prompt, session_id, cwd, stop_hook_active, transcript_path, source, host.

Outcome keys: decision ("allow", "deny" for a tool call, "block" for a prompt or a stop), reason, context, and notice
(a line shown to the person when a turn ends; Claude Code only, as systemMessage).
"""
import json, os, re

HOSTS = ("claude", "codex", "gemini", "cursor", "opencode")

GEMINI_EVENTS = {"BeforeTool": "PreToolUse", "AfterTool": "PostToolUse", "BeforeAgent": "UserPromptSubmit",
                 "AfterAgent": "Stop", "PreCompress": "PreCompact", "SessionStart": "SessionStart"}
CURSOR_EVENTS = {"beforeShellExecution": "PreToolUse", "beforeReadFile": "PreToolUse", "preToolUse": "PreToolUse",
                 "beforeMCPExecution": "PreToolUse", "afterFileEdit": "PostToolUse", "afterShellExecution": "PostToolUse",
                 "postToolUse": "PostToolUse", "postToolUseFailure": "PostToolUseFailure",
                 "beforeSubmitPrompt": "UserPromptSubmit", "stop": "Stop", "preCompact": "PreCompact",
                 "sessionStart": "SessionStart"}
OPENCODE_EVENTS = {"tool.execute.before": "PreToolUse", "tool.execute.after": "PostToolUse",
                   "session.compacting": "PreCompact"}
TOOL_NAMES = {"run_shell_command": "Bash", "shell": "Bash", "bash": "Bash", "exec_command": "Bash",
              "read_file": "Read", "read": "Read", "view": "Read", "read_many_files": "Read",
              "write_file": "Write", "write": "Write", "replace": "Edit", "edit": "Edit", "apply_patch": "Edit",
              "multiedit": "Edit", "Shell": "Bash"}
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)


def _tool_input(tool, raw):
    """Normalise argument names: file_path for paths, content for whole files, new_string for edits."""
    ti = dict(raw or {})
    for key in ("filePath", "path", "absolute_path", "file"):
        if key in ti and "file_path" not in ti and isinstance(ti[key], str):
            ti["file_path"] = ti[key]
    for src, dst in (("newString", "new_string"), ("oldString", "old_string")):
        if src in ti and dst not in ti:
            ti[dst] = ti[src]
    if tool == "Bash" and "command" not in ti:
        cmd = ti.get("cmd") or ti.get("script")
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        if cmd:
            ti["command"] = str(cmd)
    return ti


def _from_patch(raw):
    """Codex sends every file edit as apply_patch; read the patch envelope for the files and the added lines."""
    text = ""
    for v in (raw or {}).values():
        if isinstance(v, str) and "*** Begin Patch" in v:
            text = v
            break
    files = PATCH_FILE.findall(text)
    added = "\n".join(line[1:] for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    ti = {"file_path": files[0] if files else "", "new_string": added, "files": files}
    return ti


def parse(host, payload, event=None):
    p = payload or {}
    ev = {"host": host, "session_id": str(p.get("session_id") or p.get("conversation_id") or p.get("sessionID") or "unknown"),
          "cwd": p.get("cwd") or (p.get("workspace_roots") or [None])[0] or os.getcwd(),
          "transcript_path": p.get("transcript_path"), "stop_hook_active": bool(p.get("stop_hook_active")),
          "source": p.get("source"), "last_assistant_message": p.get("last_assistant_message"),
          "tool_response": p.get("tool_response"), "prompt": p.get("prompt"), "error": p.get("error"),
          "agent_id": p.get("agent_id"), "agent_type": p.get("agent_type"),
          "agent_transcript_path": p.get("agent_transcript_path")}
    name = event or p.get("hook_event_name") or p.get("event") or ""
    if host in ("claude", "codex"):
        ev["hook_event_name"] = name
        tool = p.get("tool_name") or ""
        if tool == "apply_patch":
            ev["tool_name"], ev["tool_input"] = "Edit", _from_patch(p.get("tool_input"))
        else:
            ev["tool_name"] = tool
            ev["tool_input"] = _tool_input(tool, p.get("tool_input"))
        resp = p.get("tool_response")
        if host == "codex" and name == "PostToolUse" and isinstance(resp, dict) and (resp.get("error") or resp.get("exit_code") not in (None, 0)):
            ev["hook_event_name"] = "PostToolUseFailure"
    elif host == "gemini":
        ev["hook_event_name"] = GEMINI_EVENTS.get(name, name)
        tool = TOOL_NAMES.get(p.get("tool_name") or "", p.get("tool_name") or "")
        ev["tool_name"], ev["tool_input"] = tool, _tool_input(tool, p.get("tool_input"))
        resp = p.get("tool_response")
        if name == "AfterTool" and isinstance(resp, dict) and resp.get("error"):
            ev["hook_event_name"] = "PostToolUseFailure"
        if name == "BeforeAgent":
            ev["prompt"] = p.get("prompt") or p.get("user_prompt") or ""
    elif host == "cursor":
        ev["hook_event_name"] = CURSOR_EVENTS.get(name, name)
        if name in ("beforeShellExecution", "afterShellExecution"):
            ev["tool_name"], ev["tool_input"] = "Bash", {"command": p.get("command") or ""}
            ev["tool_response"] = p.get("output")
        elif name == "beforeReadFile":
            ev["tool_name"], ev["tool_input"] = "Read", {"file_path": p.get("file_path") or ""}
        elif name == "afterFileEdit":
            edits = p.get("edits") or []
            ev["tool_name"] = "Edit"
            ev["tool_input"] = {"file_path": p.get("file_path") or "",
                                "new_string": "\n".join(str(e.get("new_string") or "") for e in edits if isinstance(e, dict))}
        else:
            tool = TOOL_NAMES.get(p.get("tool_name") or "", p.get("tool_name") or "")
            ev["tool_name"], ev["tool_input"] = tool, _tool_input(tool, p.get("tool_input"))
    elif host == "opencode":
        ev["hook_event_name"] = OPENCODE_EVENTS.get(name, name)
        tool = TOOL_NAMES.get(p.get("tool") or "", p.get("tool") or "")
        ev["tool_name"], ev["tool_input"] = tool, _tool_input(tool, p.get("args"))
        if name == "tool.execute.after":
            ev["tool_response"] = p.get("output")
    else:
        raise ValueError(f"unknown host {host!r}; expected one of {', '.join(HOSTS)}")
    ev.setdefault("tool_name", "")
    ev.setdefault("tool_input", {})
    return ev


def claude_payload(ev):
    """The canonical event as a Claude Code hook payload, for the v2 pipeline scripts and the vendored guards."""
    out = {"session_id": ev["session_id"], "cwd": ev["cwd"], "hook_event_name": ev["hook_event_name"],
           "transcript_path": ev.get("transcript_path"), "stop_hook_active": ev.get("stop_hook_active", False)}
    if ev.get("tool_name"):
        out["tool_name"], out["tool_input"] = ev["tool_name"], ev.get("tool_input") or {}
    if ev.get("tool_response") is not None:
        out["tool_response"] = ev["tool_response"]
    if ev.get("prompt") is not None:
        out["prompt"] = ev["prompt"]
    return out


def render(host, ev, outcome):
    """Return (stdout text, exit code) in the host's own contract."""
    name = ev["hook_event_name"]
    decision, reason, ctx = outcome.get("decision", "allow"), outcome.get("reason") or "", outcome.get("context") or ""
    blocked = decision in ("deny", "block")
    if blocked and not reason:
        reason = "Blocked by the harness."
    if host in ("claude", "codex"):
        if name == "PreToolUse":
            if blocked:
                return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                          "permissionDecisionReason": reason}}), 0
            if outcome.get("updated_input") is not None and host == "claude":
                out = {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": outcome["updated_input"]}
                if ctx:
                    out["additionalContext"] = ctx
                return json.dumps({"hookSpecificOutput": out}), 0
            return (json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}) if ctx else ""), 0
        if name in ("UserPromptSubmit", "Stop") and blocked:
            return json.dumps({"decision": "block", "reason": reason}), 0
        if name == "Stop" and outcome.get("notice") and host == "claude":
            return json.dumps({"systemMessage": outcome["notice"]}), 0
        if ctx and name in ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "SessionStart", "Stop"):
            wire = "PostToolUse" if host == "codex" and name == "PostToolUseFailure" else name
            return json.dumps({"hookSpecificOutput": {"hookEventName": wire, "additionalContext": ctx}}), 0
        return "", 0
    if host == "gemini":
        # Gemini parses stdout as JSON on exit 0; any stray text makes it fall back to allow, so always print JSON.
        wire = {v: k for k, v in GEMINI_EVENTS.items()}.get(name, name)
        if name == "PostToolUseFailure":
            wire = "AfterTool"
        if blocked:
            return json.dumps({"decision": "deny", "reason": reason}), 0
        if ctx:
            return json.dumps({"hookSpecificOutput": {"hookEventName": wire, "additionalContext": ctx}}), 0
        return "{}", 0
    if host == "cursor":
        if name == "PreToolUse":
            if blocked:
                return json.dumps({"permission": "deny", "user_message": reason, "agent_message": reason}), 0
            return json.dumps({"permission": "allow"}), 0
        if name == "UserPromptSubmit":
            return json.dumps({"continue": False, "user_message": reason} if blocked else {"continue": True}), 0
        if name == "Stop":
            return json.dumps({"followup_message": reason} if blocked else {}), 0
        if ctx:
            return json.dumps({"additional_context": ctx}), 0
        return "{}", 0
    if host == "opencode":
        # Read by the plugin: it throws an Error on deny and appends the context to the tool's output.
        if blocked:
            return json.dumps({"decision": "deny", "reason": reason}), 0
        return json.dumps({"context": ctx} if ctx else {}), 0
    raise ValueError(f"unknown host {host!r}")
