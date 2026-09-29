import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import tips, trajectory  # noqa: E402


def _step(sid, tool, **inp):
    trajectory.record({"hook_event_name": "PreToolUse", "session_id": sid, "cwd": "/r", "tool_name": tool,
                       "tool_input": inp}, {"decision": "allow"})


def test_repeated_reads_and_full_suites_become_tips(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path))
    for _ in range(7):
        _step("t1", "Read", file_path="/r/app/views.py")
    for _ in range(4):
        _step("t1", "Bash", command="python3 -m pytest -q")
    _step("t1", "Bash", command="python3 -m pytest -q tests/test_views.py")
    texts = [t["text"] for t in tips.compute()]
    assert any("views.py was read 7 times" in t for t in texts)
    assert any("full test suite ran 4 times" in t for t in texts)


def test_a_dismissed_tip_stays_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path))
    for _ in range(7):
        _step("t2", "Read", file_path="/r/a.py")
    key = tips.compute()[0]["key"]
    tips.dismiss(key)
    assert all(t["key"] != key for t in tips.compute())
