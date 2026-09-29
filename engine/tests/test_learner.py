import json, os, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import dispatch, learner, memory, profile, router, signals  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")
    return tmp_path


def _prompt(session, text, cwd):
    return dispatch.handle({"hook_event_name": "UserPromptSubmit", "session_id": session, "cwd": str(cwd), "prompt": text})


def test_signals_keep_no_patient_identifiers(home):
    signals.record("s", "correction", "no, the claim for John Smith DOB 04/12/1961 member W123456789 is wrong")
    text = json.dumps(signals.load())
    assert "W123456789" not in text and "04/12/1961" not in text and "John Smith" not in text


@pytest.mark.parametrize("field", ["rules/policy.json", "gate.max_blocks", "guard.secret", "engine/dispatch.py",
                                   "threshold.correction_count", "settings.json"])
def test_the_learner_can_never_write_guards_policy_gates_or_code(field):
    with pytest.raises(PermissionError):
        profile.apply_change(field, "anything", [], "try")


def test_one_correction_is_not_enough(home):
    _prompt("a", "no, don't add a new dependency for date parsing", home)
    assert learner.run()["applied"] == []


def test_repeated_corrections_across_sessions_become_one_lesson(home):
    for s in ("a", "b", "c"):
        _prompt(s, "no, don't add a new dependency for date parsing, use the standard library", home)
    out = learner.run()
    assert len(out["applied"]) == 1
    lessons = memory.load(str(home))
    assert any("dependency" in (l["do"] + l["when"]) for l in lessons)
    assert learner.run()["applied"] == []  # idempotent: the same evidence does not add it again


def test_asking_for_shorter_answers_sets_the_style_and_it_reaches_the_next_session(home):
    for s in ("a", "b", "c"):
        _prompt(s, "too long, shorter please", home)
    learner.run()
    assert profile.load()["style.brevity"] == "brief"
    start = dispatch.handle({"hook_event_name": "SessionStart", "session_id": "d", "cwd": str(home), "source": "startup"})
    assert "short answers" in start["context"] and "learn revert" in start["context"]
    again = dispatch.handle({"hook_event_name": "SessionStart", "session_id": "e", "cwd": str(home), "source": "startup"})
    assert "learn revert" not in again["context"]  # a change is announced once


def test_a_change_can_be_reverted_exactly(home):
    before = profile.load()
    cid = profile.apply_change("style.brevity", "brief", [3], "test")
    assert profile.revert(cid) and profile.load() == before


def test_learned_phrases_route_only_what_no_rule_routes(home):
    for s in ("a", "b", "c"):
        _prompt(s, f"the eligibility thingy for batch {s}", home)
        dispatch.handle({"hook_event_name": "PostToolUse", "session_id": s, "cwd": str(home), "tool_name": "Skill",
                         "tool_input": {"skill": "investigate"}})
    learner.run()
    assert "eligibility" in profile.load()["router_keywords"]["investigate"]
    assert router.route("the eligibility thingy again")["playbook"] == "investigate"
    assert router.route("fix the broken eligibility check")["playbook"] == "bug-fix"  # a rule still wins


def test_a_noisy_check_is_reported_to_the_owner_never_changed(home):
    for i in range(6):
        signals.record(f"s{i}", "gate", rule="done-gate")
    out = learner.run()
    assert out["applied"] == [] and any("done-gate" in r for r in out["report"])
    assert "done-gate" in open(os.path.join(profile._dir(), "owner-report.md")).read()


def test_the_background_learner_respects_the_switch(monkeypatch):
    assert learner.start_in_background("/bin/true") is False


def test_two_sessions_opening_together_start_one_learner(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "h"))
    monkeypatch.delenv("NODARIS_HARNESS_NO_BG", raising=False)
    started = []
    monkeypatch.setattr(learner.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert learner.start_in_background("nodaris-harness") is True
    assert learner.start_in_background("nodaris-harness") is False
    assert len(started) == 1
