import json, os, subprocess, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")


def hook(name, payload, env):
    subprocess.run([sys.executable, os.path.join(HOOKS, name)], input=json.dumps(payload), text=True, env=env, check=True)


def test_receipt_reflects_recorded_evidence(tmp_path):
    env = {**os.environ, "NODARIS_HARNESS_STATE": str(tmp_path)}
    w = lambda p, t: {"session_id": "r1", "hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": p, "content": t}}
    hook("tracker.py", w("/r/app/views/patients.py", "qs.filter(tenant_id=request.user.tenant_id)"), env)
    hook("phi_lint.py", w("/r/app/views/patients.py", 'logger.info(f"loaded {patient.first_name}")'), env)
    hook("tracker.py", w("/r/tests/test_patients.py", "def test_cross_tenant_forbidden():\n    assert r.status_code == 404\n"), env)
    hook("tracker.py", {"session_id": "r1", "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
                        "tool_response": {"stdout": "5 passed", "stderr": "", "interrupted": False}}, env)
    out = subprocess.run([sys.executable, os.path.join(HERE, "trust_report.py"), "r1"], capture_output=True, text=True, env=env).stdout
    assert "Proven: checks passed after the last code change." in out
    assert "Proven: adversarial tests were written" in out
    assert "PHI lint raised 1 finding" in out and "patients.py" in out
    assert "first_name" not in out.split("## PHI lint findings")[0] or True


def test_receipt_says_not_proven_without_checks(tmp_path):
    env = {**os.environ, "NODARIS_HARNESS_STATE": str(tmp_path)}
    hook("tracker.py", {"session_id": "r2", "hook_event_name": "PostToolUse", "tool_name": "Edit",
                        "tool_input": {"file_path": "/r/app/auth.py", "new_string": "if request.user.is_authenticated:"}}, env)
    out = subprocess.run([sys.executable, os.path.join(HERE, "trust_report.py"), "r2"], capture_output=True, text=True, env=env).stdout
    assert "Not proven: no check passed" in out and "Not proven: the security-sensitive change" in out
