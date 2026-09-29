import json, os, subprocess, sys
import pytest

HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HOOKS)
import intake, phi_lint, repocard


def run(hook, payload, tmp_path, **env):
    e = {**os.environ, "NODARIS_HARNESS_STATE": str(tmp_path / "state"), **env}
    p = subprocess.run([sys.executable, os.path.join(HOOKS, hook)], input=json.dumps(payload), capture_output=True, text=True, env=e, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def edit(sid, path, text, event="PostToolUse", tool="Write"):
    return {"session_id": sid, "hook_event_name": event, "tool_name": tool, "tool_input": {"file_path": path, "content": text}}


def bash(sid, cmd, ok=True, stdout="12 passed in 0.4s", cwd=None):
    # the real shapes: a non-zero exit arrives as PostToolUseFailure with "Exit code N" and no response
    if not ok:
        return {"session_id": sid, "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                "tool_input": {"command": cmd}, "tool_response": None, "error": "Exit code 1"}
    return {"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": cmd},
            "cwd": str(cwd) if cwd else "", "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False}}


def stop(sid, cwd, active=False):
    return {"session_id": sid, "hook_event_name": "Stop", "cwd": str(cwd), "stop_hook_active": active}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "tests").mkdir(parents=True)
    (r / "pytest.ini").write_text("[pytest]\n")
    (r / "CLAUDE.md").write_text("rules\n")
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


# ---- intake

@pytest.mark.parametrize("prompt,vague,sensitive", [
    ("patients from other clinics can see each other's stuff, fix it", True, True),
    ("the ERA upload keeps crashing on weird files from payers, make it more robust", True, True),
    ("make the denials table work with the keyboard", True, True),
    ("In `smcp/api/views/reidentify.py` bind tenant_id to request.user and run pytest", False, True),
    ("what does this repo do?", False, False),
    ("rename the button label to Save changes", True, False),
])
def test_classify(prompt, vague, sensitive):
    c = intake.classify(prompt)
    assert (c["vague"], c["sensitive"]) == (vague, sensitive)


def test_intake_first_prompt_gets_card_and_procedures(repo, tmp_path):
    out = run("intake.py", {"session_id": "s1", "prompt": "patients from other clinics can see each other's records, fix it", "cwd": str(repo)}, tmp_path)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "repo card" in ctx and "-m pytest -q" in ctx and "CLAUDE.md" in ctx
    assert "spec-first" in ctx and "self-attack" in ctx
    out2 = run("intake.py", {"session_id": "s1", "prompt": "what does this repo do?", "cwd": str(repo)}, tmp_path)
    assert out2 is None



def test_intake_robustness_ask_gets_the_hardening_pass(repo, tmp_path):
    out = run("intake.py", {"session_id": "s9", "prompt": "Payers send messed up 835 files and the import crashes. Make the parser bulletproof.",
                            "cwd": str(repo)}, tmp_path)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "hardening pass" in ctx and "truncated input" in ctx and "leaks PHI" in ctx


def test_robustness_tests_count_as_attack_tests(repo, tmp_path):
    run("tracker.py", edit("r1", str(repo / "pipeline" / "x12_835.py"), "def parse(claim_id): ...\n"), tmp_path)
    run("tracker.py", edit("r1", str(repo / "tests" / "test_x12_835.py"),
                           "def test_truncated_segment_is_rejected():\n    with pytest.raises(X12Error):\n        parse('ISA*')\n"), tmp_path)
    run("tracker.py", bash("r1", ".venv/bin/python -m pytest -q"), tmp_path)
    out = run("done_gate.py", stop("r1", repo), tmp_path)
    assert out is None or "self-attack" not in out["reason"]

# ---- repo card

def test_repocard_node(tmp_path):
    r = tmp_path / "web"
    r.mkdir()
    (r / "package.json").write_text(json.dumps({"scripts": {"test": "vitest", "lint": "eslint .", "build": "vite build"}}))
    (r / "pnpm-lock.yaml").write_text("")
    (r / "tsconfig.json").write_text("{}")
    card = repocard.detect(str(r))
    assert card["verify"] == ["pnpm run lint", "pnpm run test -- --run", "pnpm run build", "npx tsc --noEmit"]


# ---- phi lint

@pytest.mark.parametrize("path,text,expect", [
    ("app/views.py", 'logger.info(f"claim failed for {patient.first_name}")', "patient field"),
    ("app/x12.py", 'logger.warning(f"bad segment: {e}")', "exception text"),
    ("tests/test_api.py", 'PATIENT = {"first_name": "Margaret", "dob": "1971-04-23"}', "real-looking name"),
    ("tests/test_api.py", 'ssn = "123-45-6789"', "SSN-shaped"),
    ("tests/factories.py", 'email = "jane.roe@gmail.com"', "email"),
])
def test_phi_lint_flags(path, text, expect):
    assert any(expect in m for _, m in phi_lint.scan(path, text))


@pytest.mark.parametrize("path,text", [
    ("tests/test_api.py", 'PATIENT = {"first_name": "Test Patient Alpha", "dob": "1900-01-01", "ssn": "000-00-0000"}'),
    ("app/views.py", 'logger.info("reidentify denied", extra={"count": 1, "kind": "cross_tenant"})'),
    ("app/views.py", "# logger.info(patient.name) was removed"),
    ("tests/test_api.py", 'email = "test@example.com"; phone = "555-0142"'),
])
def test_phi_lint_quiet_on_safe_code(path, text):
    assert phi_lint.scan(path, text) == []


def test_phi_lint_hook_output(tmp_path):
    out = run("phi_lint.py", edit("s", "app/views.py", 'logger.info("x %s", patient.dob)'), tmp_path)
    assert "PHI lint" in out["hookSpecificOutput"]["additionalContext"]


# ---- tracker + done gate

def test_gate_quiet_without_code_changes(repo, tmp_path):
    run("tracker.py", edit("g0", str(repo / "README.md"), "docs"), tmp_path)
    assert run("done_gate.py", stop("g0", repo), tmp_path) is None


def test_gate_requires_passing_check_after_last_edit(repo, tmp_path):
    run("tracker.py", edit("g1", str(repo / "app" / "util.py"), "def f():\n    return 1\n"), tmp_path)
    out = run("done_gate.py", stop("g1", repo), tmp_path)
    assert out["decision"] == "block" and "-m pytest -q" in out["reason"]
    run("tracker.py", bash("g1", "python3 -m pytest -q", ok=False), tmp_path)
    out = run("done_gate.py", stop("g1", repo), tmp_path)
    assert out["decision"] == "block" and "failed" in out["reason"]
    run("tracker.py", bash("g1", "python3 -m pytest -q"), tmp_path)
    assert run("done_gate.py", stop("g1", repo), tmp_path) is None


def test_gate_does_not_repeat_itself(repo, tmp_path):
    run("tracker.py", edit("g2", str(repo / "app" / "util.py"), "x = 1\n"), tmp_path)
    assert run("done_gate.py", stop("g2", repo), tmp_path)["decision"] == "block"
    # told once, nothing changed: no second loop, in this turn or the next
    assert run("done_gate.py", stop("g2", repo, active=True), tmp_path) is None
    assert run("done_gate.py", stop("g2", repo), tmp_path) is None
    # new work since the block: the gate applies again
    run("tracker.py", edit("g2", str(repo / "app" / "util.py"), "x = 2\n"), tmp_path)
    assert run("done_gate.py", stop("g2", repo), tmp_path)["decision"] == "block"


def test_gate_blocks_at_most_eight_times_per_session(repo, tmp_path):
    blocked = 0
    for i in range(11):
        run("tracker.py", edit("g5", str(repo / "app" / "util.py"), f"x = {i}\n"), tmp_path)
        blocked += bool(run("done_gate.py", stop("g5", repo), tmp_path))
    assert blocked == 8


def test_gate_sensitive_change_needs_attack_tests(repo, tmp_path):
    run("tracker.py", edit("g3", str(repo / "app" / "views" / "reidentify.py"), "tenant_id = request.user.tenant_id\n"), tmp_path)
    run("tracker.py", edit("g3", str(repo / "tests" / "test_reid.py"), "def test_same_tenant_ok(): pass\n"), tmp_path)
    run("tracker.py", bash("g3", ".venv/bin/python -m pytest -q"), tmp_path)
    out = run("done_gate.py", stop("g3", repo), tmp_path)
    assert out["decision"] == "block" and "self-attack" in out["reason"] and "reidentify.py" in out["reason"]
    run("tracker.py", edit("g3", str(repo / "tests" / "test_reid.py"), "def test_cross_tenant_is_forbidden():\n    assert resp.status_code == 403\n", tool="Edit"), tmp_path)
    out = run("done_gate.py", stop("g3", repo), tmp_path)
    assert out["decision"] == "block"
    run("tracker.py", bash("g3", "cd /x && .venv/bin/python -m pytest -q 2>&1 | tail -3"), tmp_path)
    out = run("done_gate.py", stop("g3", repo), tmp_path)
    assert out["decision"] == "block" and "scan.py" in out["reason"] and "self-attack" not in out["reason"]
    scan = "python3 /cfg/tools/scan.py"
    run("tracker.py", bash("g3", scan, ok=False), tmp_path)
    assert "scan.py" in run("done_gate.py", stop("g3", repo), tmp_path)["reason"]
    # a clean scan that did not cover the sensitive file proves nothing about it
    run("tracker.py", bash("g3", scan, stdout="scan: scanned files: README.md\nscan: clean", cwd=repo), tmp_path)
    assert "--files app/views/reidentify.py" in run("done_gate.py", stop("g3", repo), tmp_path)["reason"]
    # a piped scan that hides its findings is not clean
    run("tracker.py", bash("g3", scan + " | tail -3", stdout="scan: scanned files: app/views/reidentify.py\n  app/views/reidentify.py:3: B602", cwd=repo), tmp_path)
    assert "scan.py" in run("done_gate.py", stop("g3", repo), tmp_path)["reason"]
    run("tracker.py", bash("g3", scan + " --files app/views/reidentify.py",
                           stdout="scan: scanned files: app/views/reidentify.py\nscan: clean", cwd=repo), tmp_path)
    out = run("done_gate.py", stop("g3", repo), tmp_path)
    assert out["decision"] == "block" and "threat model" in out["reason"] and "scan.py" not in out["reason"]
    # editing a document is not writing a threat model, and a note must name the changed file
    run("tracker.py", edit("g3", str(repo / "docs" / "notes.md"), "Renamed a helper.\n", tool="Edit"), tmp_path)
    run("tracker.py", edit("g3", str(repo / "docs" / "security" / "generic.md"), "# Threat model\nAbuse cases: none.\n"), tmp_path)
    assert "reidentify.py" in run("done_gate.py", stop("g3", repo), tmp_path)["reason"]
    run("tracker.py", edit("g3", str(repo / "docs" / "security" / "reid-threat-model.md"),
                           "# Threat model: reidentify\nAbuse cases: a caller sends another tenant's id.\n"), tmp_path)
    assert run("done_gate.py", stop("g3", repo), tmp_path) is None


def test_gate_ignores_non_verify_commands_and_can_be_switched_off(repo, tmp_path):
    run("tracker.py", edit("g4", str(repo / "app" / "util.py"), "x = 1\n"), tmp_path)
    run("tracker.py", bash("g4", "ls -la"), tmp_path)
    assert run("done_gate.py", stop("g4", repo), tmp_path)["decision"] == "block"
    assert run("done_gate.py", stop("g4", repo), tmp_path, NODARIS_DONE_GATE="off") is None


def events(tmp_path, sid):
    return json.load(open(tmp_path / "state" / f"{sid}.json"))["events"]


def test_tracker_success_detection(tmp_path):
    run("tracker.py", bash("g5", "npm test", ok=False), tmp_path)
    run("tracker.py", bash("g5", "python -m pytest -q | tail -2", stdout="2 failed, 10 passed in 1.2s"), tmp_path)
    run("tracker.py", bash("g5", "npx vitest run", stdout=" Tests  4 passed (4)"), tmp_path)
    got = [(e["ok"], e["ran"]) for e in events(tmp_path, "g5")]
    assert got == [(False, False), (False, False), (True, True)]


@pytest.mark.parametrize("cmd", ['echo "pytest passed"', 'git commit -m "ran pytest -q"', "grep -rn pytest .", "cat pytest.ini"])
def test_mentions_of_a_runner_are_not_checks(tmp_path, cmd):
    run("tracker.py", bash("g6", cmd), tmp_path)
    assert not (tmp_path / "state" / "g6.json").exists()


def test_empty_attack_test_does_not_count(repo, tmp_path):
    run("tracker.py", edit("g7", str(repo / "app" / "views" / "auth.py"), "tenant_id = request.user.tenant_id\n"), tmp_path)
    run("tracker.py", edit("g7", str(repo / "tests" / "test_auth.py"), "def test_cross_tenant_forbidden():\n    pass\n"), tmp_path)
    run("tracker.py", bash("g7", "pytest -q"), tmp_path)
    assert "self-attack" in run("done_gate.py", stop("g7", repo), tmp_path)["reason"]


def test_lint_alone_does_not_prove_attack_tests_ran(repo, tmp_path):
    run("tracker.py", edit("g8", str(repo / "app" / "views" / "auth.py"), "tenant_id = request.user.tenant_id\n"), tmp_path)
    run("tracker.py", edit("g8", str(repo / "tests" / "test_auth.py"), "def test_cross_tenant_forbidden():\n    assert r.status_code == 403\n"), tmp_path)
    run("tracker.py", bash("g8", "ruff check .", stdout="All checks passed!"), tmp_path)
    assert "self-attack" in run("done_gate.py", stop("g8", repo), tmp_path)["reason"]


def test_hooks_survive_garbage_input(tmp_path):
    for h in ("intake.py", "tracker.py", "phi_lint.py", "done_gate.py"):
        e = {**os.environ, "NODARIS_HARNESS_STATE": str(tmp_path / "state")}
        p = subprocess.run([sys.executable, os.path.join(HOOKS, h)], input="not json", capture_output=True, text=True, env=e)
        assert p.returncode == 0


def test_path_trigger_briefs_once_when_the_prompt_did_not(tmp_path):
    w = lambda p, t: edit("p1", p, t)
    out = run("phi_lint.py", w("/r/app/billing/remit.py", "total = claim.amount\n"), tmp_path)
    assert "path trigger" in out["hookSpecificOutput"]["additionalContext"]
    assert run("phi_lint.py", w("/r/app/billing/other.py", "x = 1\n"), tmp_path) is None
    run("intake.py", {"session_id": "p2", "prompt": "fix the login for other clinics", "cwd": str(tmp_path)}, tmp_path)
    out = run("phi_lint.py", edit("p2", "/r/app/auth/login.py", "def login(request): pass\n"), tmp_path)
    assert out is None or "path trigger" not in out["hookSpecificOutput"]["additionalContext"]
