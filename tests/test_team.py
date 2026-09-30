import json, pathlib, subprocess, sys

HARNESS = pathlib.Path(__file__).resolve().parents[1]


def build(dest):
    return subprocess.run([sys.executable, str(HARNESS / "team.py"), "build", str(dest)], capture_output=True, text=True, timeout=120)


def guard(dest, name, tool, ti):
    payload = json.dumps({"tool_name": tool, "tool_input": ti, "cwd": str(dest), "hook_event_name": "PreToolUse"})
    return subprocess.run([sys.executable, str(dest / "hooks" / name), "--mode", "claude"], input=payload,
                          capture_output=True, text=True, timeout=30).returncode


def test_team_build_is_self_contained(tmp_path):
    dest = tmp_path / "harness"
    r = build(dest)
    assert r.returncode == 0, r.stdout + r.stderr
    settings = json.loads((dest / "settings.json").read_text())
    commands = [h["command"] for es in settings["hooks"].values() for e in es for h in e["hooks"]]
    for c in commands:
        script = pathlib.Path(c.split('"')[3] if c.count('"') >= 4 else c.split('"')[1])
        assert script.is_file() and str(script).startswith(str(dest)), c
    assert not any(p.is_symlink() for p in dest.rglob("*"))
    assert "{{CONFIG}}" not in (dest / "CLAUDE.md").read_text()
    for skill in ("spec-first", "self-attack", "healthcare-domain", "healthcare-app-blueprint", "secure-build", "pocock-tdd"):
        assert (dest / "skills" / skill / "SKILL.md").is_file(), skill
    assert any("/hooks/**" in d and str(dest) in d for d in settings["permissions"]["deny"])


def test_team_guards_block_and_allow(tmp_path):
    dest = tmp_path / "harness"
    assert build(dest).returncode == 0
    assert guard(dest, "secret-guard.py", "Bash", {"command": "cat .env"}) == 2
    assert guard(dest, "secret-guard.py", "Bash", {"command": "git push --no-verify origin dev"}) == 2
    assert guard(dest, "destructive-guard.py", "Bash", {"command": "rm -rf ~"}) == 2
    assert guard(dest, "secret-guard.py", "Bash", {"command": "python3 -m pytest -q"}) == 0
    assert guard(dest, "destructive-guard.py", "Bash", {"command": "ls -la"}) == 0


def test_rebuild_over_read_only_build(tmp_path):
    dest = tmp_path / "harness"
    assert build(dest).returncode == 0
    assert build(dest).returncode == 0


def test_build_carries_nothing_from_the_owners_home(tmp_path):
    dest = tmp_path / "harness"
    assert build(dest).returncode == 0
    home = str(pathlib.Path.home())
    leaked = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file() and home in p.read_text(errors="ignore")]
    assert leaked == []
