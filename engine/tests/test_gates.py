import os, subprocess, sys, time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import dispatch, gates  # noqa: E402

GOOD_DESIGN = """# Claim export

## Options
### A. One export job per practice
Queue per tenant.
### B. One shared job filtered by tenant
Single worker.

## Chosen
A, because a tenant's export can never read another tenant's rows.

## Rejected
B: one filter bug leaks data across practices.

## Acceptance checks
- A cross-tenant export test fails closed.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    r = tmp_path / "app"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(r), "add", "a.py"], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one"], check=True)
    return str(r)


def _stop(repo, session, text, active=False):
    return {"hook_event_name": "Stop", "session_id": session, "cwd": repo, "last_assistant_message": text,
            "stop_hook_active": active}


def test_an_investigation_without_evidence_is_sent_back_once(repo):
    gates.save_route("s1", {"playbook": "investigate", "overlays": []})
    assert "no evidence" in gates.stop_check(_stop(repo, "s1", "It works because the cache is warm."))
    assert gates.stop_check(_stop(repo, "s1", "It works because the cache is warm.")) is None


def test_evidence_in_any_accepted_form_passes(repo):
    for i, text in enumerate(["Set in app/cache.py:42.", "Ran pytest, exit code 0.", "12 passed in 1.2s",
                              "Output:\n```\nOK\n```"]):
        gates.save_route(f"e{i}", {"playbook": "bug-fix", "overlays": []})
        assert gates.stop_check(_stop(repo, f"e{i}", text)) is None


def test_a_hook_already_active_never_blocks_again(repo):
    gates.save_route("s2", {"playbook": "investigate", "overlays": []})
    assert gates.stop_check(_stop(repo, "s2", "No citations here.", active=True)) is None


def test_a_sensitive_feature_needs_a_complete_design_record(repo):
    gates.save_route("s3", {"playbook": "feature", "overlays": ["tenant-money"]})
    d = os.path.join(repo, "docs", "design")
    os.makedirs(d)
    open(os.path.join(d, "2026-09-26-export.md"), "w").write("# Export\n\n## Options\n### A only\n")
    why = gates.stop_check(_stop(repo, "s3", "Done."))
    assert "tenant-money" in why and "missing section" in why
    gates.save_route("s4", {"playbook": "feature", "overlays": ["auth"]})
    path = os.path.join(d, "2026-09-26-export.md")
    open(path, "w").write(GOOD_DESIGN)
    assert "no approved review" in gates.stop_check(_stop(repo, "s4", "Done."))
    gates.record_design_review(path, "APPROVED", [], "reviewer")
    gates.save_route("s4b", {"playbook": "feature", "overlays": ["auth"]})
    assert gates.stop_check(_stop(repo, "s4b", "Done.")) is None


def test_a_design_record_from_before_the_session_does_not_count(repo):
    d = os.path.join(repo, "docs", "design")
    os.makedirs(d)
    p = os.path.join(d, "old.md")
    open(p, "w").write(GOOD_DESIGN)
    os.utime(p, (time.time() - 3600, time.time() - 3600))
    gates.save_route("s5", {"playbook": "new-app", "overlays": ["phi"]})
    assert "design record" in gates.stop_check(_stop(repo, "s5", "Done."))


def test_ordinary_features_are_not_gated(repo):
    gates.save_route("s6", {"playbook": "feature", "overlays": []})
    assert gates.stop_check(_stop(repo, "s6", "Added the button.")) is None


def test_design_check_needs_two_options():
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(GOOD_DESIGN.replace("### B. One shared job filtered by tenant\n", ""))
    ok, why = gates.design_ok(f.name)
    assert not ok and "two" in why


def test_a_review_is_bound_to_the_exact_commit(repo):
    assert gates.review_status(repo)["state"] == "none"
    rec, _ = gates.record_review(repo, "pass", "reviewer-agent")
    assert rec and gates.review_status(repo)["state"] == "pass"
    (open(os.path.join(repo, "a.py"), "a")).write("y = 2\n")
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "two"], check=True)
    st = gates.review_status(repo)
    assert st["state"] == "none" and st["commit"] != rec["commit"]


def test_a_push_approval_request_shows_the_review_state(repo):
    ev = {"hook_event_name": "PreToolUse", "session_id": "p1", "cwd": repo, "tool_name": "Bash",
          "tool_input": {"command": "git push origin feature/x"}}
    out = dispatch.pre_tool(ev)
    assert out["decision"] == "deny" and "No independent review is recorded" in out["reason"]
    gates.record_review(repo, "pass", "reviewer-agent")
    assert "passed an independent review" in dispatch.pre_tool(ev)["reason"]


def test_the_prompt_hook_remembers_the_route(repo):
    dispatch.prompt({"hook_event_name": "UserPromptSubmit", "session_id": "r1", "cwd": repo,
                     "prompt": "why does the claim list reset when I change pages?"})
    assert gates.load_route("r1")["playbook"] in ("investigate", "bug-fix")


def test_a_design_review_cannot_approve_open_serious_findings_and_goes_stale_on_edit(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    p = tmp_path / "d.md"
    p.write_text(GOOD_DESIGN)
    rec, why = gates.record_design_review(str(p), "APPROVED", [{"severity": "high", "text": "tenant filter in the view only"}], "r")
    assert rec is None and "open high or medium" in why
    assert gates.record_design_review(str(p), "APPROVED", [{"severity": "high", "resolved": True}, {"severity": "low"}], "r")[0]
    assert gates.design_approved(str(p))
    p.write_text(GOOD_DESIGN + "\nOne more line.\n")
    assert not gates.design_approved(str(p))


def test_the_first_edit_on_main_is_sent_back_once_with_the_branch_instruction(repo):
    subprocess.run(["git", "-C", repo, "branch", "-M", "main"], check=True)
    ev = {"hook_event_name": "PreToolUse", "session_id": "b1", "cwd": repo, "tool_name": "Edit",
          "tool_input": {"file_path": os.path.join(repo, "a.py"), "old_string": "x = 1", "new_string": "x = 2"}}
    out = dispatch.pre_tool(ev)
    assert out["decision"] == "deny" and out["rule"] == "branch-rule" and "git switch -c" in out["reason"]
    assert dispatch.pre_tool(ev)["decision"] == "allow"
    subprocess.run(["git", "-C", repo, "switch", "-qc", "feat/x"], check=True)
    assert dispatch.pre_tool(dict(ev, session_id="b2"))["decision"] == "allow"
