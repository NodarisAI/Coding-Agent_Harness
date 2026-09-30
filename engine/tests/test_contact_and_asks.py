"""Two changes asked for by the first teammate to test the harness (2026-09-30):

1. A contact detail on its own (an email, a phone number, an address, an account number) is not patient information.
   A prompt is stopped only when contact details come with a health cue, in bulk, or next to a health identifier;
   the person's own address, company domains and reserved test domains never count. Health identifiers (SSN, MRN,
   member and claim ids, patient names) still stop a prompt on their own.
2. Pull request and issue collaboration no longer needs a terminal approval for every call: in a permission mode
   that prompts, Claude Code's own dialog asks the person; in any mode, one approval can cover that kind of action
   on one repository for the rest of the session. Pushes, merges, deploys, messages and the rest stay per call.

Every string is synthetic. The fixture addresses deliberately use made-up domains outside the reserved test
domains (example.com, .test, .invalid), because an address on a reserved domain is exactly what the new rule lets
through; a test of the stricter path needs one that is not.
"""
import json, os, subprocess, sys, time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from nodaris_harness import dispatch, events, jev, policy, redact  # noqa: E402

PERSONAL = "fixture.tester@fixture-mailbox.io"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NODARIS_HARNESS_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    with open(tmp_path / "gitconfig", "w") as fh:
        fh.write("[user]\n\temail = owner.fixture@fixture-own-mailbox.io\n")
    monkeypatch.delenv("NODARIS_JEV", raising=False)
    for name in ("repo", "other"):
        subprocess.run(["git", "init", "-q", str(tmp_path / name)], check=True)
    return tmp_path


def blocked(text):
    return redact.redact_text(text).blocking(text)


# ---- 1. contact details ------------------------------------------------------------------------------------------

def test_an_email_address_on_its_own_does_not_stop_a_prompt():
    assert blocked("Use " + PERSONAL + " as the QA login when you test the sign-up page") == {}
    assert blocked("Call the vendor on 415-555-0142 about the build server") == {}


def test_own_company_and_test_addresses_never_count_even_next_to_a_health_word():
    for addr in ("owner.fixture@fixture-own-mailbox.io", "someone@nodaris.ai", "qa@example.com", "qa@clinic.test"):
        assert blocked("Send the patient portal invite test to " + addr) == {}, addr


def test_a_contact_detail_with_a_health_cue_still_stops_the_prompt():
    got = blocked("The patient's email is " + PERSONAL + ", why was her claim denied?")
    assert got.get("EMAIL") == 1
    assert blocked("DOB 01/02/1990, email " + PERSONAL) != {}


def test_contact_details_in_bulk_stop_the_prompt():
    rows = ", ".join("fixture.person%d@fixture-mailbox.io" % i for i in range(3))
    assert blocked("Import these users: " + rows) != {}


def test_health_identifiers_stop_a_prompt_on_their_own():
    ssn = "-".join(("123", "45", "6789"))
    assert blocked("Look up SSN " + ssn + " in the export") != {}
    assert blocked("Why was member id W123456789 denied?") != {}


def test_the_prompt_hook_lets_a_lone_email_through_and_still_stops_patient_contact_details(env):
    ok = dispatch.prompt(events.parse("claude", {"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                                                 "cwd": str(env / "repo"),
                                                 "prompt": "Use " + PERSONAL + " as the QA login for the sign-up test"}))
    assert ok.get("decision") != "block"
    stop = dispatch.prompt(events.parse("claude", {"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                                                   "cwd": str(env / "repo"),
                                                   "prompt": "The patient's email is " + PERSONAL + ", fix her claim"}))
    assert stop["decision"] == "block" and PERSONAL not in stop["reason"]


def test_jev_gets_a_lone_email_masked_never_as_written():
    r = redact.redact_text("Use " + PERSONAL + " as the QA login for the sign-up page test please")
    assert PERSONAL not in r.text and r.blocking("x") == {}


# ---- 2. approvals ------------------------------------------------------------------------------------------------

def pre(env, command, mode=None, session="s1", repo="repo"):
    p = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
         "session_id": session, "cwd": str(env / repo)}
    if mode:
        p["permission_mode"] = mode
    ev = events.parse("claude", p)
    out = dispatch.handle(ev)
    return ev, out, json.loads(events.render("claude", ev, out)[0] or "{}")


def test_pr_and_issue_collaboration_uses_claude_codes_own_dialog_when_the_mode_prompts(env):
    for mode in ("default", "acceptEdits", "plan", "auto"):
        _, out, wire = pre(env, "gh pr create --fill", mode)
        hs = wire["hookSpecificOutput"]
        assert hs["permissionDecision"] == "ask", mode
        assert "C-COLLAB" in hs["permissionDecisionReason"]
    _, _, wire = pre(env, "gh issue comment 12 --body done", "default")
    assert wire["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_without_a_prompting_mode_the_terminal_approval_stays(env):
    for mode in ("bypassPermissions", "dontAsk", None):
        _, _, wire = pre(env, "gh pr create --fill", mode)
        hs = wire["hookSpecificOutput"]
        assert hs["permissionDecision"] == "deny" and "nodaris-harness approve" in hs["permissionDecisionReason"], mode


def test_pushes_merges_and_releases_never_become_a_dialog(env):
    for cmd in ("git push origin feature/x", "gh pr merge 4 --merge", "gh release create v1", "gh api -X POST repos/o/r/x"):
        _, _, wire = pre(env, cmd, "default")
        assert wire["hookSpecificOutput"]["permissionDecision"] == "deny", cmd


def test_a_session_grant_covers_collaboration_on_that_repository_for_that_session_only(env):
    ev, out, _ = pre(env, "gh pr create --fill", "bypassPermissions")
    h = out["reason"].split("nodaris-harness approve ")[1].split()[0]
    ok, msg = policy.approve(h, "fixture-approver", lambda rec: "session")
    assert ok and "session" in msg
    assert pre(env, "gh pr create --fill", "bypassPermissions")[1]["decision"] == "allow"
    assert pre(env, "gh pr comment 4 --body looks-good", "bypassPermissions")[1]["decision"] == "allow"
    assert pre(env, "gh issue create --title x --body y", "bypassPermissions")[1]["decision"] == "allow"
    assert pre(env, "gh pr comment 4 --body x", "bypassPermissions", session="s2")[1]["decision"] == "deny"
    assert pre(env, "gh pr comment 4 --body x", "bypassPermissions", repo="other")[1]["decision"] == "deny"
    assert pre(env, "git push origin feature/x", "bypassPermissions")[1]["decision"] == "deny"
    assert pre(env, "gh pr merge 4 --merge", "bypassPermissions")[1]["decision"] == "deny"


def test_a_session_answer_never_grants_a_per_call_rule(env):
    _, out, _ = pre(env, "git push origin feature/x", "bypassPermissions")
    h = out["reason"].split("nodaris-harness approve ")[1].split()[0]
    ok, msg = policy.approve(h, "fixture-approver", lambda rec: "session")
    assert not ok and "once" in msg
    assert pre(env, "git push origin feature/x", "bypassPermissions")[1]["decision"] == "deny"


def test_a_tampered_or_expired_grant_is_ignored(env, monkeypatch):
    _, out, _ = pre(env, "gh pr create --fill", "bypassPermissions")
    h = out["reason"].split("nodaris-harness approve ")[1].split()[0]
    assert policy.approve(h, "fixture-approver", lambda rec: "session")[0]
    d = policy._dirs()["approvals"]
    path = next(os.path.join(d, f) for f in os.listdir(d) if f.startswith("grant-"))
    rec = json.load(open(path))
    rec["session"] = "s2"
    json.dump(rec, open(path, "w"))
    assert pre(env, "gh pr comment 4 --body x", "bypassPermissions", session="s2")[1]["decision"] == "deny"
    rec["session"] = "s1"
    json.dump(rec, open(path, "w"))
    assert pre(env, "gh pr comment 4 --body x", "bypassPermissions")[1]["decision"] == "allow"
    monkeypatch.setattr(policy, "GRANT_TTL", -1)
    assert pre(env, "gh pr comment 4 --body x", "bypassPermissions")[1]["decision"] == "deny"


def test_the_terminal_prompt_offers_the_session_choice_only_for_grantable_rules(env):
    _, out, _ = pre(env, "gh pr create --fill", "bypassPermissions")
    h = out["reason"].split("nodaris-harness approve ")[1].split()[0]
    rec = json.load(open(os.path.join(policy._dirs()["pending"], h + ".json")))
    assert rec["grantable"] is True and rec["session"] == "s1" and rec["scope"] == os.path.realpath(str(env / "repo"))
    _, out, _ = pre(env, "git push origin feature/y", "bypassPermissions")
    h = out["reason"].split("nodaris-harness approve ")[1].split()[0]
    assert json.load(open(os.path.join(policy._dirs()["pending"], h + ".json")))["grantable"] is False


def test_the_policy_version_moved_with_the_rule_change():
    assert policy.load_policy()["version"] == "0.3-draft"
