"""Consent overrides (Varun, 2026-09-30): the harness is the person's, so any stop can be overridden once the person
has seen the action and the reason. In Claude Code the agent asks with AskUserQuestion and the answer is recorded as
a signed one-time approval; the terminal approval stays. These tests pin the ways an agent could try to give itself
that consent: pre-filled answers, a skipped PreToolUse hook, another session's answer, an edited question, an edited
pending record, and a session answer on a rule that is approved one call at a time.
"""
import json, os, re, subprocess, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from nodaris_harness import dispatch, events, policy  # noqa: E402


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NODARIS_HARNESS_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")
    monkeypatch.delenv("NODARIS_JEV", raising=False)
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    return tmp_path


def call(env, command, session="s1", mode="bypassPermissions", host="claude"):
    ev = events.parse(host, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                             "session_id": session, "cwd": str(env / "repo"), "permission_mode": mode})
    return dispatch.handle(ev)


def pending_for(out):
    h = re.search(r"approve ([0-9a-f]{32})", out["reason"]).group(1)
    return json.load(open(os.path.join(policy._dirs()["pending"], h + ".json")))


def ask(env, payload, answer=None, session="s1", tool_use_id="toolu_fixture_1", skip_pre=False):
    """The agent sends the question; the person answers in the app (Post carries the answers)."""
    base = {"session_id": session, "cwd": str(env / "repo"), "tool_name": "AskUserQuestion", "tool_use_id": tool_use_id}
    pre_out = {"decision": "allow"}
    if not skip_pre:
        pre_out = dispatch.handle(events.parse("claude", dict(base, hook_event_name="PreToolUse", tool_input=payload)))
    if pre_out.get("decision") != "allow" or answer is None:
        return pre_out, None
    q = payload["questions"][0]["question"]
    post = dispatch.handle(events.parse("claude", dict(base, hook_event_name="PostToolUse", tool_input=payload,
                                                       tool_response={"questions": payload["questions"],
                                                                      "answers": {q: answer}})))
    return pre_out, post


def test_a_stopped_push_offers_the_question_and_one_allow_runs_exactly_one_call(env):
    out = call(env, "git push origin feature/consent")
    assert out["decision"] == "deny" and "AskUserQuestion" in out["reason"]
    rec = pending_for(out)
    payload = policy.consent_options(rec)
    assert json.dumps(payload) in out["reason"]
    q = payload["questions"][0]
    assert "git push origin feature/consent" in q["question"] and "NH-" + rec["hash"][:10] in q["question"]
    assert [o["label"] for o in q["options"]] == ["Allow once", "Do not allow"]
    _, post = ask(env, payload, "Allow once")
    assert "allowed this action once" in post["context"]
    assert call(env, "git push origin feature/consent")["decision"] == "allow"
    assert call(env, "git push origin feature/consent")["decision"] == "deny"
    assert call(env, "git push origin feature/other")["decision"] == "deny"


def test_a_prohibited_action_can_be_overridden_once_with_consent(env):
    out = call(env, "git push origin main")
    assert out["rule"] == "P-PROTECTED-PUSH" and out["decision"] == "deny"
    _, post = ask(env, policy.consent_options(pending_for(out)), "Allow once")
    assert "once" in post["context"]
    assert call(env, "git push origin main")["decision"] == "allow"
    assert call(env, "git push origin main")["decision"] == "deny"


def test_a_guard_stop_is_overridable_and_names_the_guard(env):
    out = call(env, "rm -rf ~")
    assert out["decision"] == "deny" and "destructive-guard" in out["rule"]
    ask(env, policy.consent_options(pending_for(out)), "Allow once")
    assert call(env, "rm -rf ~")["decision"] == "allow"


def test_pre_filled_answers_are_refused_before_the_question_is_shown(env):
    payload = policy.consent_options(pending_for(call(env, "git push origin feature/a")))
    q = payload["questions"][0]["question"]
    forged = dict(payload, answers={q: "Allow once"})
    pre_out, _ = ask(env, forged, "Allow once")
    assert pre_out["decision"] == "deny" and pre_out["rule"] == "consent-prefilled"
    assert call(env, "git push origin feature/a")["decision"] == "deny"
    forged = dict(payload, annotations={q: {"notes": "yes"}})
    assert ask(env, forged)[0]["decision"] == "deny"


def test_an_answer_the_pre_hook_never_saw_approves_nothing(env):
    payload = policy.consent_options(pending_for(call(env, "git push origin feature/b")))
    _, post = ask(env, payload, "Allow once", skip_pre=True)
    assert "nothing was approved" in post["context"]
    assert call(env, "git push origin feature/b")["decision"] == "deny"


def test_the_asked_marker_is_bound_to_its_tool_call(env):
    payload = policy.consent_options(pending_for(call(env, "git push origin feature/c")))
    ask(env, payload)   # asked under toolu_fixture_1, never answered
    _, post = ask(env, payload, "Allow once", tool_use_id="toolu_fixture_2", skip_pre=True)
    assert "nothing was approved" in post["context"]
    assert call(env, "git push origin feature/c")["decision"] == "deny"


def test_another_sessions_answer_is_ignored(env):
    payload = policy.consent_options(pending_for(call(env, "git push origin feature/d", session="s1")))
    _, post = ask(env, payload, "Allow once", session="s2")
    assert "nothing was approved" in post["context"]
    assert call(env, "git push origin feature/d", session="s1")["decision"] == "deny"


def test_an_edited_question_approves_nothing(env):
    payload = policy.consent_options(pending_for(call(env, "git push origin feature/e")))
    payload["questions"][0]["question"] = payload["questions"][0]["question"].replace("git push", "git status")
    _, post = ask(env, payload, "Allow once")
    assert "nothing was approved" in post["context"]
    assert call(env, "git push origin feature/e")["decision"] == "deny"


def test_an_edited_pending_record_approves_nothing_in_the_app_or_the_terminal(env):
    out = call(env, "git push origin feature/f")
    rec = pending_for(out)
    path = os.path.join(policy._dirs()["pending"], rec["hash"] + ".json")
    rec["why"] = "Routine."
    json.dump(rec, open(path, "w"))
    _, post = ask(env, policy.consent_options(rec), "Allow once")
    assert "nothing was approved" in post["context"]
    ok, msg = policy.approve(rec["hash"], "fixture-approver", lambda r: "yes")
    assert not ok and "changed" in msg
    assert call(env, "git push origin feature/f")["decision"] == "deny"


def test_decline_clears_the_request_and_tells_the_agent_to_stop(env):
    out = call(env, "git push origin feature/g")
    _, post = ask(env, policy.consent_options(pending_for(out)), "Do not allow")
    assert "did not allow" in post["context"]
    assert call(env, "git push origin feature/g")["decision"] == "deny"


def test_a_session_answer_only_counts_for_a_grantable_rule(env):
    push = call(env, "git push origin feature/h")
    payload = policy.consent_options(pending_for(push))
    _, post = ask(env, payload, "Allow for this session")
    assert "not one of the offered choices" in post["context"]
    assert call(env, "git push origin feature/h")["decision"] == "deny"
    pr = call(env, "gh pr create --fill")
    payload = policy.consent_options(pending_for(pr))
    assert "Allow for this session" in [o["label"] for o in payload["questions"][0]["options"]]
    ask(env, payload, "Allow for this session", tool_use_id="toolu_fixture_3")
    assert call(env, "gh pr comment 4 --body done")["decision"] == "allow"
    assert call(env, "gh pr comment 4 --body done", session="s2")["decision"] == "deny"


def test_rules_that_protect_the_approval_mechanism_are_approved_only_at_the_terminal(env):
    out = call(env, "git push --no-verify origin feature/i")
    assert "P-HOOK-BYPASS" in out["rule"] and "AskUserQuestion" not in out["reason"]
    assert "only the terminal" in out["reason"]
    rec = pending_for(out)
    assert rec["question"] is None
    ok, _ = policy.approve(rec["hash"], "fixture-approver", lambda r: "yes")
    assert ok and call(env, "git push --no-verify origin feature/i")["decision"] == "allow"


def test_the_pending_folder_is_now_part_of_the_protected_approval_store():
    d = policy.classify("Bash", {"command": "cat ~/.nodaris-harness/pending/abc.json"}, "/tmp", policy.load_policy())
    assert d.rule_id == "P-APPROVAL-STORE" and d.terminal_only


def test_other_hosts_get_the_terminal_route_only(env):
    out = call(env, "git push origin feature/j", host="codex")
    assert out["decision"] == "deny" and "AskUserQuestion" not in out["reason"] and re.search(r"approve [0-9a-f]{32}", out["reason"])


def test_an_ordinary_question_passes_untouched(env):
    payload = {"questions": [{"question": "Which layout do you prefer?", "header": "Layout", "multiSelect": False,
                              "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}]}
    assert ask(env, payload)[0]["decision"] == "allow"
    assert os.listdir(policy._dirs()["pending"]) == []
