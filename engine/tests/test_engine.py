"""Engine features behind the hooks: episodes and datasets, compaction snapshots, lessons."""
import json, os, subprocess, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BIN = os.path.join(ROOT, "bin", "nodaris-harness")
sys.path.insert(0, os.path.join(ROOT, "engine"))
from nodaris_harness import compact, memory, trajectory  # noqa: E402

SECRET_VALUES = ["W123456789", "John Smith", "04/12/1961", "555-0199"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(home))
    e = dict(os.environ, NODARIS_HARNESS_HOME=str(home), NODARIS_HARNESS_STATE=str(tmp_path / "state"))
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    e["_proj"] = str(proj)
    return e


def hook(env, payload, host="claude"):
    p = subprocess.run([sys.executable, BIN, "hook", "--host", host], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, cwd=env["_proj"], timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def session(env, sid="s1"):
    base = {"session_id": sid, "cwd": env["_proj"]}
    hook(env, {**base, "hook_event_name": "UserPromptSubmit", "prompt": "Add a claim-status endpoint for the payer portal"})
    hook(env, {**base, "hook_event_name": "PreToolUse", "tool_name": "Write",
               "tool_input": {"file_path": "status.py", "content": "# member id W123456789 phone (212) 555-0199\n"}})
    hook(env, {**base, "hook_event_name": "PostToolUse", "tool_name": "Write",
               "tool_input": {"file_path": "status.py", "content": "x = 1\n"}, "tool_response": {"success": True}})
    hook(env, {**base, "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
               "tool_input": {"command": "python3 -m pytest -q"}, "tool_response": None, "error": "1 failed"})
    hook(env, {**base, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat .env"}})
    hook(env, {**base, "hook_event_name": "Stop", "stop_hook_active": False})
    return base


def test_episode_is_recorded_redacted_and_exports_as_sft_and_eval(env, tmp_path):
    session(env)
    files = os.listdir(os.path.join(env["NODARIS_HARNESS_HOME"], "episodes"))
    assert len(files) == 1
    raw = open(os.path.join(env["NODARIS_HARNESS_HOME"], "episodes", files[0])).read()
    for v in SECRET_VALUES:
        assert v not in raw
    sft, ev = tmp_path / "sft.jsonl", tmp_path / "eval.jsonl"
    assert trajectory.export("sft", str(sft)) == 1
    assert trajectory.export("eval", str(ev)) == 1
    rec = json.loads(sft.read_text())
    assert rec["schema"] == "nodaris-harness/sft"
    roles = [m["role"] for m in rec["messages"]]
    assert roles[0] == "user" and "assistant" in roles and "harness" in roles
    assert rec["outcome"]["refused_calls"] == 1 and rec["outcome"]["stop_blocks"] == 1
    e = json.loads(ev.read_text())
    assert e["task"].startswith("Add a claim-status endpoint")
    assert any(r["rule"] == "secret-guard" for r in e["harness_required"])


def test_compaction_snapshot_keeps_asks_changes_and_open_failures(env):
    base = session(env, "s2")
    hook(env, {**base, "hook_event_name": "PreCompact", "trigger": "auto"})
    out = hook(env, {**base, "hook_event_name": "SessionStart", "source": "compact"})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "Add a claim-status endpoint for the payer portal" in ctx
    assert "status.py" in ctx and "python3 -m pytest -q" in ctx
    assert "W123456789" not in ctx
    assert compact.restore("never-seen") == ""


def test_lessons_are_recalled_once_by_prompt_and_by_file(env, tmp_path):
    proj = env["_proj"]
    memory.add(proj, "adding an X12 835 parser path", "wrap a fragment in a synthetic ISA envelope before testing it",
               why="a headerless fragment scans clean", keywords=["835", "parser", "envelope"], files=["parsers/*.py"])
    assert os.path.exists(os.path.join(proj, ".nodaris-harness", "lessons.jsonl"))
    first = memory.recall_for_prompt("s3", proj, "Fix the 835 parser so the envelope check passes")
    assert "synthetic ISA envelope" in first
    assert memory.recall_for_prompt("s3", proj, "Fix the 835 parser so the envelope check passes") == ""
    assert "synthetic ISA envelope" in memory.recall_for_file("s4", proj, "parsers/era.py")
    assert memory.recall_for_prompt("s5", proj, "rename the button on the settings page") == ""


def test_a_lesson_never_stores_patient_identifiers(env):
    lesson, path = memory.add(env["_proj"], "member id W123456789 failed eligibility", "check the payer id first")
    assert "W123456789" not in open(path).read()


def test_the_same_router_brief_is_not_repeated_within_a_session(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")
    from nodaris_harness import dispatch
    ev = {"hook_event_name": "UserPromptSubmit", "session_id": "r1", "cwd": str(tmp_path)}
    first = dispatch.handle({**ev, "prompt": "fix the bug where the export button crashes"})
    again = dispatch.handle({**ev, "prompt": "fix the bug where the export button still crashes on save"})
    assert "Harness router" in first.get("context", "")
    assert "Harness router" not in again.get("context", "")
    dispatch.handle({"hook_event_name": "SessionStart", "session_id": "r1", "cwd": str(tmp_path), "source": "compact"})
    after = dispatch.handle({**ev, "prompt": "fix the bug where the export button crashes"})
    assert "Harness router" in after.get("context", "")
