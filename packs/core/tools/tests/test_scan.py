import pathlib, shutil, subprocess, sys

import pytest

SCAN = pathlib.Path(__file__).resolve().parents[1] / "scan.py"
needs = pytest.mark.skipif(not (shutil.which("gitleaks") and shutil.which("bandit")), reason="gitleaks and bandit not installed")


def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("fixture\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    return tmp_path


def run(path):
    return subprocess.run([sys.executable, str(SCAN), str(path)], capture_output=True, text=True, timeout=600)


def test_nothing_changed_is_clean(tmp_path):
    assert run(repo(tmp_path)).returncode == 0


@needs
def test_committing_does_not_escape_the_scan(tmp_path):
    r = repo(tmp_path)
    (r / "app.py").write_text("import subprocess\n\ndef run(name):\n    return subprocess.call('ls ' + name, shell=True)\n")
    subprocess.run(["git", "add", "app.py"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "change"], cwd=r, check=True)
    out = run(r)
    assert out.returncode == 1 and "app.py" in out.stdout


@needs
def test_injection_and_token_are_found_without_printing_the_value(tmp_path):
    r = repo(tmp_path)
    token = "ghp_" + "".join("aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zA1bC3"[i] for i in range(36))
    (r / "app.py").write_text("import subprocess\n\ndef run(name):\n    return subprocess.call('ls ' + name, shell=True)\n\n"
                              f"GITHUB_TOKEN = '{token}'\n")
    out = run(r)
    assert out.returncode == 1
    assert token not in out.stdout + out.stderr
    assert "app.py" in out.stdout


@needs
def test_clean_change_passes(tmp_path):
    r = repo(tmp_path)
    (r / "util.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    assert run(r).returncode == 0


@needs
def test_first_commit_is_scanned_not_reported_clean(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("import subprocess\n\ndef run(name):\n    return subprocess.call('ls ' + name, shell=True)\n")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "first"], cwd=tmp_path, check=True)
    out = run(tmp_path)
    assert out.returncode == 1 and "app.py" in out.stdout


@needs
def test_named_files_cover_earlier_commits(tmp_path):
    r = repo(tmp_path)
    (r / "app.py").write_text("import subprocess\n\ndef run(name):\n    return subprocess.call('ls ' + name, shell=True)\n")
    for msg, extra in (("change", "app.py"), ("docs", "NOTES.md")):
        if extra == "NOTES.md":
            (r / "NOTES.md").write_text("notes\n")
        subprocess.run(["git", "add", extra], cwd=r, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", msg], cwd=r, check=True)
    assert "app.py" not in run(r).stdout.split("scan: scanned files: ")[-1].splitlines()[0]
    named = subprocess.run([sys.executable, str(SCAN), str(r), "--files", str(r / "app.py")], capture_output=True, text=True, timeout=600)
    assert named.returncode == 1 and "scan: scanned files: app.py" in named.stdout
