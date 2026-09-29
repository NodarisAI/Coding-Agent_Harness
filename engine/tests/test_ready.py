import json, os, subprocess, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import dispatch, ready  # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")


def _repo(tmp_path, checks, name="Version 1"):
    root = tmp_path / "repo"
    (root / ".nodaris").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "README.md").write_text("Install the Nodaris harness\n")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    path = root / ".nodaris" / "acceptance.json"
    path.write_text(json.dumps({"name": name, "checks": checks}))
    return root, str(path)


def test_each_kind_of_check_passes_and_fails(tmp_path):
    root, path = _repo(tmp_path, [
        {"id": "run-ok", "title": "Command passes", "run": [sys.executable, "-c", "pass"]},
        {"id": "run-bad", "title": "Command fails", "run": [sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"]},
        {"id": "file", "title": "File exists", "files": ["README.md"]},
        {"id": "nofile", "title": "Missing file", "files": ["INSTALL.md"]},
        {"id": "has", "title": "Contains", "contains": {"README.md": ["Nodaris harness"]}},
        {"id": "hasnt", "title": "Does not contain", "contains": {"README.md": ["setup prompt"]}},
        {"id": "absent", "title": "No marker", "absent": ["Generated with"]},
    ])
    _, results = ready.evaluate(path)
    status = {r["id"]: r["status"] for r in results}
    assert status == {"run-ok": "pass", "run-bad": "fail", "file": "pass", "nofile": "fail", "has": "pass",
                      "hasnt": "fail", "absent": "pass"}
    bad = next(r for r in results if r["id"] == "run-bad")
    assert "Exit code 3" in bad["detail"] and "boom" in bad["detail"]


def test_a_tracked_marker_fails_the_absent_check(tmp_path):
    root, path = _repo(tmp_path, [{"id": "absent", "title": "No marker", "absent": ["Generated with"]}])
    (root / "NOTES.md").write_text("Generated with a tool\n")
    subprocess.run(["git", "-C", str(root), "add", "NOTES.md"], check=True)
    _, results = ready.evaluate(path)
    assert results[0]["status"] == "fail" and "NOTES.md" in results[0]["detail"]


def test_verdict_separates_failures_from_steps_only_a_person_can_take(tmp_path):
    root, path = _repo(tmp_path, [
        {"id": "ok", "title": "Passes", "files": ["README.md"]},
        {"id": "owner", "title": "Guards installed", "owner": "Varun", "action": "Run the guard installer.",
         "files": ["missing.txt"]},
    ])
    _, results = ready.evaluate(path)
    code, line = ready.verdict(results)
    assert code == 2 and "Varun: Run the guard installer." in line
    code, _ = ready.verdict(results + [{"id": "x", "title": "x", "status": "fail", "detail": "", "owner": None,
                                        "action": None, "seconds": 0}])
    assert code == 1


@pytest.mark.parametrize("bad, message", [
    ({"checks": []}, "non-empty"),
    ({"checks": [{"id": "a", "title": "A"}]}, "names no test"),
    ({"checks": [{"id": "a", "title": "A", "run": "rm -rf /"}]}, "list of arguments"),
    ({"checks": [{"id": "a", "title": "A", "files": []}, {"id": "a", "title": "B", "files": []}]}, "used twice"),
])
def test_bad_manifests_are_named(tmp_path, bad, message):
    p = tmp_path / "a.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ready.ManifestError, match=message):
        ready.load(str(p))


def _stop(session):
    return dispatch.handle({"hook_event_name": "Stop", "session_id": session, "cwd": ".", "stop_hook_active": False})


def test_the_armed_stop_hook_keeps_the_agent_working_until_the_list_passes(tmp_path):
    root, path = _repo(tmp_path, [{"id": "doc", "title": "Install guide", "files": ["INSTALL.md"]}])
    ready.arm("s1", path)
    first = ready.stop_check("s1")
    assert first and "Install guide" in first and "Round 1" in first
    (root / "INSTALL.md").write_text("ok")
    assert ready.stop_check("s1") is None
    assert ready.stop_check("s1") is None  # disarmed once done


def test_the_loop_gives_up_after_the_round_limit(tmp_path):
    _, path = _repo(tmp_path, [{"id": "doc", "title": "Install guide", "files": ["INSTALL.md"]}])
    ready.arm("s2", path)
    rounds = [ready.stop_check("s2") for _ in range(ready.MAX_BLOCKS + 2)]
    assert all(rounds[:ready.MAX_BLOCKS]) and rounds[ready.MAX_BLOCKS] is None and rounds[-1] is None


def test_only_person_steps_left_lets_the_session_finish(tmp_path):
    _, path = _repo(tmp_path, [{"id": "g", "title": "Guards", "owner": "Varun", "files": ["nope"]}])
    ready.arm("s3", path)
    assert ready.stop_check("s3") is None


def test_the_hook_arms_the_session_that_runs_ready_arm(tmp_path):
    root, path = _repo(tmp_path, [{"id": "doc", "title": "Install guide", "files": ["INSTALL.md"]}])
    ev = {"hook_event_name": "PreToolUse", "session_id": "s4", "cwd": str(root), "tool_name": "Bash",
          "tool_input": {"command": "bin/nodaris-harness ready --arm"}}
    dispatch._arm_ready(ev)
    assert "Install guide" in (ready.stop_check("s4") or "")
    dispatch._arm_ready({**ev, "tool_input": {"command": "nodaris-harness ready --disarm"}})
    assert ready.stop_check("s4") is None


def test_an_unarmed_session_is_never_held(tmp_path):
    assert ready.stop_check("never-armed") is None
