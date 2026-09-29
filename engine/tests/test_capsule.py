import json, os, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import capsule, dispatch, events, gates  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    (tmp_path / "app" / ".planning").mkdir(parents=True)
    (tmp_path / "app" / "DESIGN.md").write_text("# Tokens\n")
    (tmp_path / "app" / ".planning" / "COPY-STANDARD.md").write_text("# Copy\n")
    (tmp_path / "app" / "AGENTS.md").write_text("# Rules\n")
    return tmp_path


def _launch(home, session="s1", prompt="Build the claims table."):
    return {"hook_event_name": "PreToolUse", "session_id": session, "cwd": str(home / "app"), "tool_name": "Task",
            "tool_input": {"description": "claims table", "prompt": prompt, "subagent_type": "general-purpose"}}


def test_every_launch_carries_the_capsule_with_design_system_paths(home):
    gates.save_route("s1", {"playbook": "feature", "overlays": ["phi"]})
    out = dispatch.pre_tool(_launch(home))
    p = out["updated_input"]["prompt"]
    assert p.startswith("Build the claims table.") and capsule.MARK in p
    assert "DESIGN.md" in p and ".planning/COPY-STANDARD.md" in p and "AGENTS.md" in p and "phi" in p
    text, code = events.render("claude", events.parse("claude", _launch(home)), out)
    spec = json.loads(text)["hookSpecificOutput"]
    assert code == 0 and spec["permissionDecision"] == "allow" and capsule.MARK in spec["updatedInput"]["prompt"]


def test_the_capsule_is_added_once_and_never_carries_file_contents(home):
    (home / "app" / "DESIGN.md").write_text("SECRET-TOKEN-VALUE\n")
    first = dispatch.pre_tool(_launch(home))["updated_input"]["prompt"]
    again = dispatch.pre_tool(_launch(home, prompt=first))
    assert "updated_input" not in again and "SECRET-TOKEN-VALUE" not in first


def test_a_returning_subagent_is_checkpointed_and_the_budget_refuses_past_the_cap(home):
    ret = dict(_launch(home), hook_event_name="PostToolUse",
               tool_response={"totalTokens": 400_000, "totalToolUseCount": 30, "status": "completed"})
    ctx = dispatch.post_tool(ret)["context"]
    assert "400,000 tokens" in ctx and "Checkpoint" in ctx and "300,000" in ctx
    out = dispatch.pre_tool(_launch(home))
    assert out["decision"] == "deny" and out["rule"] == "subagent-budget" and "budget --add" in out["reason"]
    capsule.add_budget(200_000)
    assert dispatch.pre_tool(_launch(home))["decision"] == "allow"


def test_the_plan_sets_the_budget(home):
    os.makedirs(os.environ["NODARIS_HARNESS_HOME"], exist_ok=True)
    with open(os.path.join(os.environ["NODARIS_HARNESS_HOME"], "settings.json"), "w") as f:
        json.dump({"plan": "pro"}, f)
    assert capsule.budget() == 150_000


def test_a_subagent_gets_a_pulse_and_is_stopped_at_the_cap(home):
    ev = {"hook_event_name": "PreToolUse", "session_id": "s2", "cwd": str(home / "app"), "tool_name": "Read",
          "tool_input": {"file_path": str(home / "app" / "AGENTS.md")}, "agent_id": "ag1"}
    outs = [dispatch.pre_tool(dict(ev)) for _ in range(capsule.TOOL_CAP)]
    assert "Re-read your brief" in outs[capsule.PULSE - 1].get("context", "")
    assert outs[-1]["decision"] == "deny" and outs[-1]["rule"] == "subagent-cap"
    assert all(o["decision"] == "allow" for o in outs[:-1])
    other = dict(ev, agent_id="ag2")
    assert dispatch.pre_tool(other)["decision"] == "allow"
