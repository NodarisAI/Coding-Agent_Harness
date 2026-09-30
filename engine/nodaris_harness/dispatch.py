"""One process per hook event: guards, rules of engagement, redaction, the v2 pipeline, memory and the recorder.

Order for a tool call about to run: the secret and destructive-command guards, then the policy classes. A prohibited
call is refused with the rule and the reason. A consequential call runs only with a person's one-time approval of
that exact call; without one it is refused with the command the person runs to approve it. Everything else runs.

The engine never refuses ordinary work, and it never fails closed on its own bugs for routine calls: an internal
error in a pipeline step is recorded and the call proceeds, except for the guards and the policy, whose errors refuse
the call (a safety check that cannot run is not a pass).
"""
import json, os, re, subprocess, sys

from . import capsule, compact, copylint, designlint, events, gates, learner, memory, onboard, policy, profile, redact, router, signals, trajectory

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT_TIMEOUT = 60
CLI = os.path.join(ROOT, "bin", "nodaris-harness")


def _first_dir(*candidates):
    return next((c for c in candidates if os.path.isdir(c)), candidates[0])


HOOKS_DIR = os.environ.get("NODARIS_HARNESS_HOOKS") or _first_dir(os.path.join(ROOT, "hooks"), os.path.join(ROOT, "packs", "core", "hooks"), os.path.join(ROOT, "pack", "hooks"))
GUARDS_DIR = os.environ.get("NODARIS_HARNESS_GUARDS") or _first_dir(os.path.join(ROOT, "hooks"), os.path.join(ROOT, "packs", "core", "vendor", "guards"), os.path.join(ROOT, "pack", "vendor", "guards"))

GUARDS = [("secret-guard.py", {"Bash", "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob"}),
          ("destructive-guard.py", {"Bash", "Write", "Edit"})]
POST_STEPS = [("tracker.py", {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Task", "Agent"}),
              ("phi_lint.py", {"Write", "Edit", "MultiEdit", "NotebookEdit"})]
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _run(script_dir, name, payload, args=()):
    path = os.path.join(script_dir, name)
    if not os.path.exists(path):
        return None
    p = subprocess.run([sys.executable, path, *args], input=json.dumps(payload), capture_output=True, text=True,
                       timeout=SCRIPT_TIMEOUT)
    return p.returncode, p.stdout, p.stderr


def _json(text):
    try:
        return json.loads(text) if text and text.strip() else {}
    except ValueError:
        return {}


def _context_of(out):
    return ((_json(out).get("hookSpecificOutput") or {}).get("additionalContext") or "").strip()


def _approval_text(decision, ev):
    msg = (f"This action needs a person's approval under the rules of engagement ({decision.rule_id}: {decision.why}) "
           f"It has not run. Ask the person to approve it in their own terminal with:\n\n"
           f"    {CLI} approve {decision.action_hash}\n\n"
           f"then repeat exactly the same call. Any change to the call needs a new approval, and an approval is used once.")
    if decision.rule_id in ("C-PUSH", "C-PUBLISH", "C-DEPLOY"):
        msg += " Review state: " + gates.review_status(ev.get("cwd") or ".")["text"]
    if decision.rule_id == "C-PHI-SOURCE":
        path = (ev.get("tool_input") or {}).get("file_path") or "FILE"
        msg += (f" To work without approval, read a copy in which every identifier is replaced by a realistic "
                f"stand-in: {CLI} redact {path}")
    return msg


AGENT_TOOLS = ("Task", "Agent")


def _setting(key, default):
    try:
        with open(os.path.join(policy.home(), "settings.json")) as f:
            data = json.load(f)
        return data.get(key, default) if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def pre_tool(ev):
    try:
        beat = capsule.pulse(ev)
    except Exception:  # noqa: BLE001  guidance must never block on its own failure
        beat = None
    if beat and beat["decision"] == "deny":
        return beat
    out = _pre_tool(ev)
    if beat and out.get("decision") == "allow":
        out["context"] = "\n\n".join(x for x in (out.get("context"), beat.get("context")) if x)
    return out


READY_ARM = re.compile(r"(?:^|[\s/])nodaris-harness\s+ready\b[^;&|]*\s--(arm|disarm)\b")


def _arm_ready(ev):
    """The agent runs `nodaris-harness ready --arm`; only the hook knows its session, so the hook arms the loop."""
    cmd = str((ev.get("tool_input") or {}).get("command") or "")
    m = READY_ARM.search(cmd)
    if not m:
        return
    from . import ready
    try:
        if m.group(1) == "disarm":
            ready.disarm(ev["session_id"])
            return
        found = re.search(r"--manifest\s+(\S+)", cmd)
        manifest = found.group(1).strip("'\"") if found else ready.DEFAULT
        ready.arm(ev["session_id"], os.path.join(ev.get("cwd") or ".", os.path.expanduser(manifest)))
    except Exception:  # noqa: BLE001  arming is a convenience; it must never block the command
        pass


def _pre_tool(ev):
    if ev["tool_name"] in AGENT_TOOLS and not ev.get("agent_id"):
        try:
            return capsule.before_launch(ev)
        except Exception:  # noqa: BLE001  the capsule is guidance; a broken one must not stop the launch
            return {"decision": "allow"}
    payload = events.claude_payload(ev)
    for name, tools in GUARDS:
        if ev["tool_name"] in tools:
            res = _run(GUARDS_DIR, name, payload, ("--mode", "claude"))
            if res is None or res[0] not in (0, 2):
                # A guard that is missing or crashed has not checked the call; that is never a pass.
                return {"decision": "deny", "rule": "guard-error",
                        "reason": f"The {name[:-3]} check could not run, so this call did not run. Run `{CLI} doctor --host claude`."}
            if res[0] == 2:
                why = (_json(res[1]).get("hookSpecificOutput") or {}).get("permissionDecisionReason") or res[2].strip()
                return {"decision": "deny", "rule": name.replace(".py", ""), "reason": why}
    if ev["tool_name"] in EDIT_TOOLS and _setting("branch_rule", True):
        try:
            why = gates.branch_check(ev)
        except Exception:  # noqa: BLE001
            why = None
        if why:
            return {"decision": "deny", "rule": "branch-rule", "reason": why}
    if ev["tool_name"] in ("Bash", "shell", "run_shell_command") and not ev.get("agent_id"):
        _arm_ready(ev)
    pol = policy.load_policy()
    decision = policy.classify(ev["tool_name"], ev.get("tool_input"), ev["cwd"], pol)
    if decision.cls == "prohibited":
        return {"decision": "deny", "rule": decision.rule_id,
                "reason": (f"Refused under the rules of engagement ({decision.rule_id}): {decision.why} An agent never "
                           f"runs this. If it is really needed, the person does it themselves.")}
    if decision.cls == "consequential":
        if policy.consume_approval(decision.action_hash):
            return {"decision": "allow", "rule": decision.rule_id, "approved": True}
        policy.write_pending(decision, ev["tool_name"], ev.get("tool_input"), ev["cwd"])
        return {"decision": "deny", "rule": decision.rule_id, "reason": _approval_text(decision, ev)}
    return {"decision": "allow"}


def _fresh(session, *parts):
    """Keep only the context pieces not already given in this session. The same router brief, intake notice or lint
    advice is never injected twice; after a compaction the record is cleared, so what was lost is given again."""
    import hashlib
    path = os.path.join(gates._sdir(session), "shown.json")
    shown = gates._read(path, {})
    shown = shown if isinstance(shown, dict) else {}
    out = []
    for text in parts:
        if not text:
            continue
        key = hashlib.sha256(text.encode()).hexdigest()[:16]
        if key in shown:
            continue
        shown[key] = 1
        out.append(text)
    if out:
        try:
            gates._write(path, shown)
        except OSError:
            pass
    return out


def forget_shown(session):
    try:
        os.remove(os.path.join(gates._sdir(session), "shown.json"))
    except OSError:
        pass


def prompt(ev):
    pol = policy.load_policy()
    text = ev.get("prompt") or ""
    if pol.get("prompt_phi", "block") == "block" and text:
        r = redact.redact_text(text)
        if r.verdict == "refused":
            return {"decision": "block", "rule": "R-DATA-PROMPT",
                    "reason": "This message could not be checked for patient information, so it was not sent. Shorten it or send it in parts."}
        if r.strong:
            kinds = ", ".join(f"{k.lower().replace('_', ' ')} ({n})" for k, n in sorted(r.strong.items()))
            return {"decision": "block", "rule": "R-DATA-PROMPT",
                    "reason": (f"This message looks like it contains patient information: {kinds}. It was not sent to the "
                               f"model. Below is the same message with every identifier replaced by a realistic stand-in; "
                               f"send that instead, or use synthetic data.\n\n{r.text}")}
    parts = []
    decision = router.route(text)
    parts.append(router.brief(decision, CLI))   # given once per session per route; see _fresh
    if signals.is_correction(text):
        signals.record(ev["session_id"], "correction", text, playbook=decision["playbook"])
    elif not decision["playbook"]:
        signals.record(ev["session_id"], "unrouted", text)
    try:
        gates.save_route(ev["session_id"], decision)
    except OSError:
        pass
    res = _run(HOOKS_DIR, "intake.py", events.claude_payload(ev))
    if res:
        parts.append(_context_of(res[1]))
    parts.append(memory.recall_for_prompt(ev["session_id"], ev["cwd"], text))
    fresh = _fresh(ev["session_id"], *parts)
    fresh.append(_jev(ev, text))   # per message by design, so it is not deduplicated
    return {"decision": "allow", "context": "\n\n".join(x for x in fresh if x), "route": decision}


def _jev(ev, text):
    try:
        from . import jev
        return jev.for_prompt(ev, text, os.path.join(gates._sdir(ev["session_id"]), "jev-seen"))
    except Exception:  # noqa: BLE001  Jev is best effort and never costs the prompt its other context
        return ""


def post_tool(ev):
    parts = []
    payload = events.claude_payload(ev)
    for name, tools in POST_STEPS:
        if ev["tool_name"] in tools and (name != "phi_lint.py" or ev["hook_event_name"] == "PostToolUse"):
            res = _run(HOOKS_DIR, name, payload)
            if res:
                parts.append(_context_of(res[1]))
    if ev["tool_name"] in AGENT_TOOLS and ev["hook_event_name"] == "PostToolUse" and not ev.get("agent_id"):
        try:
            parts.append(capsule.after_return(ev))
        except Exception:  # noqa: BLE001
            pass
    if ev["tool_name"] == "Skill" and ev["hook_event_name"] == "PostToolUse":
        signals.record(ev["session_id"], "skill", name=str((ev.get("tool_input") or {}).get("skill") or "")[:60])
    if ev["tool_name"] in EDIT_TOOLS and ev["hook_event_name"] == "PostToolUse":
        ti = ev.get("tool_input") or {}
        written = "\n".join([ti.get("content") or "", ti.get("new_string") or ""] +
                             [e.get("new_string") or "" for e in ti.get("edits") or [] if isinstance(e, dict)])
        parts.append(copylint.advice(ti.get("file_path") or "", written))
        parts.append(designlint.advice(ti.get("file_path") or "", written))
    if ev["tool_name"] in EDIT_TOOLS:
        parts.append(memory.recall_for_file(ev["session_id"], ev["cwd"], (ev.get("tool_input") or {}).get("file_path")))
    return {"decision": "allow", "context": "\n\n".join(_fresh(ev["session_id"], *parts))}


def stop(ev):
    res = _run(HOOKS_DIR, "done_gate.py", events.claude_payload(ev))
    if res:
        out = _json(res[1])
        if out.get("decision") == "block":
            return {"decision": "block", "rule": "done-gate", "reason": out.get("reason") or ""}
    why = gates.stop_check(ev, CLI)
    if why:
        return {"decision": "block", "rule": "route-gate", "reason": why}
    try:
        from . import ready
        why = ready.stop_check(ev["session_id"], CLI)
    except Exception:  # noqa: BLE001
        why = None
    if why:
        return {"decision": "block", "rule": "acceptance", "reason": why}
    from . import statusline
    return {"decision": "allow", "notice": statusline.turn_notice(ev)}


def handle(ev):
    name = ev["hook_event_name"]
    try:
        if name == "PreToolUse":
            outcome = pre_tool(ev)
        elif name == "UserPromptSubmit":
            outcome = prompt(ev)
        elif name in ("PostToolUse", "PostToolUseFailure"):
            outcome = post_tool(ev)
        elif name == "Stop":
            outcome = stop(ev)
        elif name == "PreCompact":
            trajectory.record(ev, {"decision": "allow"})
            compact.snapshot(ev["session_id"])
            return {"decision": "allow"}
        elif name == "SubagentStop":
            try:
                outcome = capsule.on_subagent_stop(ev)
            except Exception:  # noqa: BLE001
                outcome = {"decision": "allow"}
        elif name == "SessionStart":
            if ev.get("source") in ("compact", "resume"):
                forget_shown(ev["session_id"])
            ctx = compact.restore(ev["session_id"]) if ev.get("source") in ("compact", "resume") else ""
            learned = profile.summary() if ev.get("source") in (None, "startup", "clear") else ""
            if ev.get("source") in (None, "startup") and not onboard.is_onboarded() and onboard.claim_first_offer():
                learned = "\n\n".join(x for x in (onboard.app_instructions(CLI), learned) if x)
            learner.start_in_background(CLI)
            if ev.get("host") == "claude":
                from . import monitor, usage
                monitor.write_link(os.environ.get("NODARIS_PANEL_LINK"), ev.get("transcript_path"), ev["session_id"], ev["cwd"])
                usage.start_in_background(CLI)
            try:
                from . import sync
                sync.start_in_background(CLI)
            except Exception:  # noqa: BLE001  sharing is best effort; it never holds up a session
                pass
            outcome = {"decision": "allow", "context": "\n\n".join(x for x in (learned, ctx) if x)}
        else:
            outcome = {"decision": "allow"}
    except Exception as exc:  # noqa: BLE001
        if name == "PreToolUse":
            outcome = {"decision": "deny", "rule": "engine-error",
                       "reason": f"The harness could not check this call ({type(exc).__name__}), so it did not run. Run `{CLI} doctor`."}
        else:
            outcome = {"decision": "allow", "rule": "engine-error"}
    if outcome.get("decision") in ("deny", "block") and outcome.get("rule"):
        signals.record(ev.get("session_id"), "gate", rule=outcome["rule"])
    trajectory.record(ev, outcome)
    return outcome


def main(host, event=None, stdin=None):
    raw = (stdin or sys.stdin).read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    ev = events.parse(host, payload, event)
    out, code = events.render(host, ev, handle(ev))
    if out:
        sys.stdout.write(out)
    return code
