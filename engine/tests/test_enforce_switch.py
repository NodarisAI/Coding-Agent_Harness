"""The enforce setting (Varun, 2026-09-30): the harness is the person's, so they can turn its stops into warnings,
all of them or only the ones they name. Every string here is synthetic."""
import json, os, subprocess, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from nodaris_harness import cli, dispatch, events, policy  # noqa: E402

SSN = "-".join(("123", "45", "6789"))


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NODARIS_HARNESS_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")
    monkeypatch.delenv("NODARIS_JEV", raising=False)
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    return tmp_path


def settings(**kw):
    os.makedirs(policy.home(), exist_ok=True)
    with open(os.path.join(policy.home(), "settings.json"), "w") as fh:
        json.dump(kw, fh)


def push(env):
    return dispatch.handle(events.parse("claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                                   "tool_input": {"command": "git push origin feature/x"},
                                                   "session_id": "s1", "cwd": str(env / "repo"),
                                                   "permission_mode": "bypassPermissions"}))


def prompt(env):
    return dispatch.handle(events.parse("claude", {"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                                                   "cwd": str(env / "repo"),
                                                   "prompt": "Look up SSN " + SSN + " in the export"}))


def test_stops_are_enforced_by_default(env):
    assert push(env)["decision"] == "deny"
    assert prompt(env)["decision"] == "block"


def test_enforce_off_turns_every_stop_into_a_warning(env):
    settings(enforce=False)
    out = push(env)
    assert out["decision"] == "allow" and out["relaxed"] and "C-PUSH" in out["context"]
    assert "did not run" not in out["context"] and "push leaves this machine" in out["context"]
    out = prompt(env)
    assert out["decision"] == "allow" and "patient information" in out["context"]
    assert SSN not in out["context"]


def test_a_list_relaxes_only_the_named_rules(env):
    settings(enforce=["R-DATA-PROMPT"])
    assert prompt(env)["decision"] == "allow"
    assert push(env)["decision"] == "deny"


def test_a_combined_stop_is_relaxed_only_when_every_rule_is_named(env):
    settings(enforce=["secret-guard"])
    out = dispatch.handle(events.parse("claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                                  "tool_input": {"command": "git push --no-verify origin feature/x"},
                                                  "session_id": "s1", "cwd": str(env / "repo")}))
    assert out["decision"] == "deny"


def test_a_relaxed_stop_is_still_recorded(env):
    settings(enforce=False)
    push(env)
    with open(os.path.join(policy.home(), "signals", os.listdir(os.path.join(policy.home(), "signals"))[0])) as fh:
        kinds = [json.loads(l).get("kind") for l in fh]
    assert "relaxed" in kinds


def test_the_warning_reaches_the_agent_on_the_wire(env):
    settings(enforce=False)
    ev = events.parse("claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                 "tool_input": {"command": "git push origin feature/x"},
                                 "session_id": "s1", "cwd": str(env / "repo")})
    wire = json.loads(events.render("claude", ev, dispatch.handle(ev))[0])
    assert "permissionDecision" not in wire["hookSpecificOutput"]
    assert "warning only" in wire["hookSpecificOutput"]["additionalContext"]


def test_a_broken_settings_file_keeps_enforcement_on(env):
    os.makedirs(policy.home(), exist_ok=True)
    with open(os.path.join(policy.home(), "settings.json"), "w") as fh:
        fh.write("{not json")
    assert push(env)["decision"] == "deny"


def test_the_cli_switch_writes_the_setting_and_keeps_the_others(env, capsys):
    settings(plan="max")
    assert cli.main(["enforce", "off"]) == 0
    data = json.load(open(os.path.join(policy.home(), "settings.json")))
    assert data == {"plan": "max", "enforce": False}
    assert cli.main(["enforce", "on"]) == 0
    assert json.load(open(os.path.join(policy.home(), "settings.json"))) == {"plan": "max", "enforce": True}
    assert cli.main(["enforce", "relax", "done-gate", "R-DATA-PROMPT"]) == 0
    assert json.load(open(os.path.join(policy.home(), "settings.json")))["enforce"] == ["done-gate", "R-DATA-PROMPT"]
    assert cli.main(["enforce", "status"]) == 0
    assert "done-gate" in capsys.readouterr().out
