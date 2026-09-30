"""Contract tests: each host's real payload shape goes through the engine and comes back in that host's contract.

Payload shapes come from harness/hosts/CONTRACTS.md (Codex schema fixtures, Gemini and Cursor docs, the OpenCode
plugin types, the Claude Code hook docs). Installs run against a temporary home and must uninstall byte-identically.
"""
import json, os, subprocess, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BIN = os.path.join(ROOT, "bin", "nodaris-harness")
sys.path.insert(0, os.path.join(ROOT, "engine"))
from nodaris_harness import hosts, policy  # noqa: E402

PHI_PROMPT = "Why was member id W123456789 for John Smith, DOB 04/12/1961, denied?"


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = dict(os.environ, NODARIS_HARNESS_HOME=str(tmp_path / "home"), NODARIS_HARNESS_STATE=str(tmp_path / "state"))
    monkeypatch.setenv("NODARIS_HARNESS_HOME", e["NODARIS_HARNESS_HOME"])
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    e["_proj"] = str(proj)
    return e


def hook(env, host, payload, event=None):
    args = [sys.executable, BIN, "hook", "--host", host] + (["--event", event] if event else [])
    p = subprocess.run(args, input=json.dumps(payload), capture_output=True, text=True, env=env, cwd=env["_proj"], timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


# ---- Claude Code and Codex ------------------------------------------------------------------------------------

def test_claude_denies_with_permission_decision_and_adds_context_on_prompt(env):
    out = hook(env, "claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat .env"},
                               "session_id": "c1", "cwd": env["_proj"]})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    out = hook(env, "claude", {"hook_event_name": "UserPromptSubmit", "prompt": "add a cross-tenant check to the claims view",
                               "session_id": "c1", "cwd": env["_proj"]})
    assert out == {} or "additionalContext" in out.get("hookSpecificOutput", {})


def test_prompt_with_patient_identifiers_is_stopped_and_handed_back_with_stand_ins(env):
    out = hook(env, "claude", {"hook_event_name": "UserPromptSubmit", "prompt": PHI_PROMPT, "session_id": "c2", "cwd": env["_proj"]})
    assert out["decision"] == "block"
    assert "W123456789" not in out["reason"] and "John Smith" not in out["reason"]
    assert "member id" in out["reason"] and "denied?" in out["reason"]


def test_codex_apply_patch_with_an_ai_mark_is_refused(env):
    patch = "*** Begin Patch\n*** Update File: CHANGELOG.md\n@@\n+Co-Authored-By: Claude <noreply@anthropic.com>\n*** End Patch\n"
    out = hook(env, "codex", {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"command": patch},
                              "session_id": "x1", "cwd": env["_proj"], "turn_id": "t", "tool_use_id": "u", "model": "m",
                              "permission_mode": "default", "transcript_path": None})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "P-AI-MARK" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_codex_stop_after_an_unchecked_code_edit_is_blocked(env):
    base = {"session_id": "x2", "cwd": env["_proj"], "turn_id": "t", "model": "m", "permission_mode": "default"}
    patch = "*** Begin Patch\n*** Add File: app.py\n+def f():\n+    return 1\n*** End Patch\n"
    hook(env, "codex", {**base, "hook_event_name": "PostToolUse", "tool_name": "apply_patch", "tool_input": {"command": patch},
                        "tool_response": "Success"})
    out = hook(env, "codex", {**base, "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": "Done."})
    assert out.get("decision") == "block" and out["reason"]


# ---- Gemini CLI -----------------------------------------------------------------------------------------------

def test_gemini_before_tool_deny_and_before_agent_phi_block(env):
    out = hook(env, "gemini", {"hook_event_name": "BeforeTool", "tool_name": "read_file", "tool_input": {"file_path": ".env"},
                               "session_id": "g1", "cwd": env["_proj"], "timestamp": "2026-09-26T00:00:00Z"})
    assert out == {"decision": "deny", "reason": out["reason"]} and out["reason"]
    out = hook(env, "gemini", {"hook_event_name": "BeforeAgent", "prompt": PHI_PROMPT, "session_id": "g1", "cwd": env["_proj"]})
    assert out["decision"] == "deny" and "W123456789" not in out["reason"]
    out = hook(env, "gemini", {"hook_event_name": "BeforeTool", "tool_name": "run_shell_command",
                               "tool_input": {"command": "ls"}, "session_id": "g1", "cwd": env["_proj"]})
    assert out == {}


# ---- Cursor ---------------------------------------------------------------------------------------------------

def test_cursor_permission_shape_prompt_gate_and_stop_followup(env):
    base = {"conversation_id": "k1", "generation_id": "g", "workspace_roots": [env["_proj"]]}
    out = hook(env, "cursor", {**base, "hook_event_name": "beforeShellExecution", "command": "git push origin main",
                               "cwd": env["_proj"]}, "beforeShellExecution")
    assert out["permission"] == "deny" and out["agent_message"]
    assert hook(env, "cursor", {**base, "hook_event_name": "beforeShellExecution", "command": "ls", "cwd": env["_proj"]},
                "beforeShellExecution") == {"permission": "allow"}
    out = hook(env, "cursor", {**base, "hook_event_name": "beforeSubmitPrompt", "prompt": PHI_PROMPT}, "beforeSubmitPrompt")
    assert out["continue"] is False
    hook(env, "cursor", {**base, "hook_event_name": "afterFileEdit", "file_path": os.path.join(env["_proj"], "app.py"),
                         "edits": [{"old_string": "", "new_string": "def f():\n    return 1\n"}]}, "afterFileEdit")
    out = hook(env, "cursor", {**base, "hook_event_name": "stop", "status": "completed"}, "stop")
    assert out.get("followup_message")


# ---- OpenCode -------------------------------------------------------------------------------------------------

def test_opencode_before_throws_on_deny_and_after_returns_context_or_nothing(env):
    out = hook(env, "opencode", {"tool": "bash", "sessionID": "o1", "callID": "c", "args": {"command": "rm -rf ~"},
                                 "cwd": env["_proj"]}, "tool.execute.before")
    assert out["decision"] == "deny"
    out = hook(env, "opencode", {"tool": "read", "sessionID": "o1", "callID": "c", "args": {"filePath": "README.md"},
                                 "cwd": env["_proj"]}, "tool.execute.before")
    assert out == {}


# ---- approvals across hosts ------------------------------------------------------------------------------------

def test_consequential_push_runs_once_after_a_terminal_approval(env):
    call = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push origin feature/intake"},
            "session_id": "a1", "cwd": env["_proj"]}
    out = hook(env, "claude", call)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "nodaris-harness approve" in reason or "/bin/nodaris-harness approve" in reason
    h = reason.split("nodaris-harness approve ")[1].split()[0]
    ok, _ = policy.approve(h, "tester", lambda rec: "yes")
    assert ok
    assert hook(env, "claude", call) == {}
    assert hook(env, "claude", call)["hookSpecificOutput"]["permissionDecision"] == "deny"
    changed = dict(call, tool_input={"command": "git push origin feature/other"})
    assert hook(env, "claude", changed)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_approve_refuses_without_a_terminal(env):
    p = subprocess.run([sys.executable, BIN, "approve", "0" * 32], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, start_new_session=True)
    assert p.returncode == 2 and "person" in p.stderr


def run_in_terminal(env, args, answer):
    """Run the CLI with a pseudo-terminal as its controlling terminal, type answer at the prompt, return (code, output)."""
    import pty, select
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(env["_proj"])
        os.execve(sys.executable, [sys.executable, BIN] + args, env)
    out, sent = b"", False
    while True:
        if not select.select([fd], [], [], 30)[0]:
            os.kill(pid, 9)
            break
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
        if not sent and b"Type yes" in out:
            os.write(fd, answer.encode() + b"\n")
            sent = True
    os.close(fd)
    return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]), out.decode(errors="replace")


def test_approve_in_a_real_terminal_reads_the_answer(env):
    call = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push origin feature/tty"},
            "session_id": "t1", "cwd": env["_proj"]}
    reason = hook(env, "claude", call)["hookSpecificOutput"]["permissionDecisionReason"]
    h = reason.split("nodaris-harness approve ")[1].split()[0]
    code, out = run_in_terminal(env, ["approve", h], "yes")
    assert code == 0 and "approved once" in out, out
    assert hook(env, "claude", call) == {}


def test_approve_in_a_real_terminal_refuses_anything_but_yes(env):
    call = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push origin feature/no"},
            "session_id": "t2", "cwd": env["_proj"]}
    reason = hook(env, "claude", call)["hookSpecificOutput"]["permissionDecisionReason"]
    h = reason.split("nodaris-harness approve ")[1].split()[0]
    code, out = run_in_terminal(env, ["approve", h], "no")
    assert code == 1 and "not approved" in out, out
    assert hook(env, "claude", call)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_security_scope_in_a_real_terminal_is_signed(env):
    code, out = run_in_terminal(env, ["security", "scope", "--repo", env["_proj"], "--env", "staging=https://staging.example.com"], "yes")
    assert code == 0 and "Signed scope written" in out, out


# ---- installs ------------------------------------------------------------------------------------------------

ORIGINAL = {"claude": ("settings.json", {"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "/usr/local/bin/my-own-hook"}]}]}}),
            "codex": ("hooks.json", {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}]}}),
            "gemini": ("settings.json", {"theme": "dark", "context": {"fileName": ["AGENTS.md"]}}),
            "cursor": ("hooks.json", {"version": 1, "hooks": {"stop": [{"command": "./audit.sh"}]}})}


@pytest.mark.parametrize("host", ["claude", "codex", "gemini", "cursor", "opencode"])
def test_install_then_uninstall_is_byte_identical_and_dry_run_writes_nothing(env, tmp_path, host):
    cfg = tmp_path / f"cfg-{host}"
    cfg.mkdir()
    before = {}
    if host in ORIGINAL:
        name, data = ORIGINAL[host]
        (cfg / name).write_text(json.dumps(data, indent=4) + "\n")
    rules = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "gemini": "GEMINI.md", "opencode": "AGENTS.md"}.get(host)
    if rules:
        (cfg / rules).write_text("# My own rules\n\nKeep answers short.\n")
    for dirpath, _, files in os.walk(cfg):
        for f in files:
            before[os.path.join(dirpath, f)] = open(os.path.join(dirpath, f), "rb").read()
    home = tmp_path / "userhome"
    rep = hosts.install(host, user_home=str(home), config_dir=str(cfg), dry_run=True)
    assert rep["diff"]
    snapshot = {p: open(p, "rb").read() for p in before}
    assert snapshot == before and not home.exists()
    hosts.install(host, user_home=str(home), config_dir=str(cfg), skills=False)
    installed = [p for p, *_ in hosts.plan(host, str(home), str(cfg))[0]]
    for p in installed:
        assert "nodaris-harness" in open(p).read()
    hosts.uninstall(host)
    after = {}
    for dirpath, _, files in os.walk(cfg):
        for f in files:
            after[os.path.join(dirpath, f)] = open(os.path.join(dirpath, f), "rb").read()
    assert after == before


def test_install_keeps_the_users_own_hooks_and_is_idempotent(env, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "settings.json").write_text(json.dumps(ORIGINAL["claude"][1]))
    hosts.install("claude", config_dir=str(cfg), skills=False)
    hosts.install("claude", config_dir=str(cfg), skills=False)
    data = json.load(open(cfg / "settings.json"))
    pre = data["hooks"]["PreToolUse"]
    assert any("my-own-hook" in json.dumps(g) for g in pre)
    assert sum("nodaris-harness" in json.dumps(g) for g in pre) == 1
    assert data["model"] == "opus"
    hosts.uninstall("claude")
    assert json.load(open(cfg / "settings.json")) == ORIGINAL["claude"][1]


def test_git_hooks_install_and_block_a_protected_push(env, tmp_path):
    proj = env["_proj"]
    hosts.install("git", project=proj)
    hook_file = os.path.join(proj, ".git", "hooks", "pre-push")
    assert os.access(hook_file, os.X_OK)
    p = subprocess.run([sys.executable, BIN, "gitcheck", "--stage", "pre-push"], cwd=proj, env=env, capture_output=True,
                       text=True, input="refs/heads/main 1111111 refs/heads/main 0000000\n")
    assert p.returncode == 1 and "protected branch main" in p.stderr
    hosts.uninstall("git")
    assert not os.path.exists(hook_file)


def test_uninstall_keeps_edits_made_to_a_git_hook_after_install(env, tmp_path):
    proj = env["_proj"]
    hook_file = os.path.join(proj, ".git", "hooks", "pre-push")
    with open(hook_file, "w") as fh:
        fh.write("#!/bin/sh\necho team check\n")
    hosts.install("git", project=proj)
    with open(hook_file, "a") as fh:
        fh.write("echo added after install\n")
    out = hosts.uninstall("git")
    assert open(hook_file).read() == "#!/bin/sh\necho team check\n"
    saved = hook_file + ".edited-after-install"
    assert "echo added after install" in open(saved).read()
    assert any(saved in k for k in out["kept"])


def test_a_guard_that_crashes_refuses_the_call_instead_of_letting_it_through(env, tmp_path):
    guards = tmp_path / "guards"
    guards.mkdir()
    (guards / "secret-guard.py").write_text("raise SystemExit(1)\n")
    (guards / "destructive-guard.py").write_text("import sys; sys.exit(0)\n")
    e = dict(env, NODARIS_HARNESS_GUARDS=str(guards))
    out = hook(e, "claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
                             "session_id": "g", "cwd": env["_proj"]})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "could not run" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_status_line_is_added_only_when_none_exists_and_removed_on_uninstall(tmp_path, monkeypatch):
    import json
    from nodaris_harness import hosts
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    cfg = tmp_path / "claude"
    hosts.install("claude", config_dir=str(cfg), skills=False)
    data = json.loads((cfg / "settings.json").read_text())
    assert data["statusLine"]["command"].endswith("statusline")
    hosts.uninstall("claude")
    assert not (cfg / "settings.json").exists() or "statusLine" not in json.loads((cfg / "settings.json").read_text() or "{}")
    (cfg / "settings.json").write_text(json.dumps({"model": "x"}))
    hosts.install("claude", config_dir=str(cfg), skills=False)
    hosts.uninstall("claude")
    assert json.loads((cfg / "settings.json").read_text()) == {"model": "x"}
    (cfg / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "mine"}}))
    hosts.install("claude", config_dir=str(cfg), skills=False)
    assert json.loads((cfg / "settings.json").read_text())["statusLine"]["command"] == "mine"


def test_claude_install_adds_the_core_deny_rules_and_uninstall_removes_only_those(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "h"))
    cfg = tmp_path / ".claude"
    cfg.mkdir()
    mine = {"permissions": {"deny": ["Read(~/.netrc)", "Bash(curl:*)"], "allow": ["Bash(ls:*)"]}, "theme": "dark"}
    (cfg / "settings.json").write_text(json.dumps(mine, indent=2))
    original = (cfg / "settings.json").read_bytes()
    hosts.install("claude", user_home=str(tmp_path), config_dir=str(cfg), skills=False)
    deny = json.loads((cfg / "settings.json").read_text())["permissions"]["deny"]
    assert deny[:2] == ["Read(~/.netrc)", "Bash(curl:*)"]
    assert "Read(~/.aws/**)" in deny and "Read(//**/.env)" in deny
    assert deny.count("Read(~/.netrc)") == 1
    hosts.uninstall("claude")
    assert (cfg / "settings.json").read_bytes() == original


def test_deny_rules_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "h"))
    (tmp_path / "h").mkdir()
    (tmp_path / "h" / "settings.json").write_text(json.dumps({"deny_rules": False}))
    cfg = tmp_path / ".claude"
    hosts.install("claude", user_home=str(tmp_path), config_dir=str(cfg), skills=False)
    assert "permissions" not in json.loads((cfg / "settings.json").read_text())


def test_rules_name_healthcare_skills_only_for_healthcare_installs():
    general, health = hosts.rules_text(packs=["core"]), hosts.rules_text(packs=["core", "healthcare"])
    assert "healthcare-domain" not in general and "healthcare-app-blueprint" not in general
    assert "healthcare-domain" in health and "healthcare-app-blueprint" in health
    assert "pack:" not in general + health and "\n\n- **Sensitive file" not in general
