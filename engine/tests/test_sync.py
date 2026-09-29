import json, os, subprocess, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import memory, policy, signals, sync  # noqa: E402


def _settings(home, **team):
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "settings.json"), "w") as f:
        json.dump({"packs": ["core"], "role": "engineer", "team_sync": team}, f)


def test_sync_is_off_unless_the_person_opted_in(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "h"))
    _settings(str(tmp_path / "h"), enabled=False)
    code, msg = sync.run(dry_run=True)
    assert code == 1 and "off" in msg


def test_the_bundle_carries_lessons_and_counts_but_no_prompt_text(tmp_path, monkeypatch):
    home = str(tmp_path / "h")
    monkeypatch.setenv("NODARIS_HARNESS_HOME", home)
    _settings(home, enabled=True, repo="acme/team-data", handle="test.one")
    memory.add(policy.home(), "When the export is slow", "Batch the query", scope="user")
    signals.record("s1", "correction", "no, the claim for Test Patient One is wrong", playbook="bug-fix")
    code, text = sync.run(dry_run=True)
    data = json.loads(text)
    assert code == 0 and data["lessons"][0]["do"] == "Batch the query"
    assert data["counts"] == {"correction:bug-fix": 1} and "Test Patient" not in text


def test_sync_pushes_only_to_the_persons_own_branch(tmp_path, monkeypatch):
    home = str(tmp_path / "h")
    monkeypatch.setenv("NODARIS_HARNESS_HOME", home)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    _settings(home, enabled=True, repo="acme/team-data", handle="test.one", url=str(remote))
    real, pushed = sync._git, []

    def fake(cwd, *args, **kw):
        if args[:1] == ("push",):
            pushed.append(args)
            return 0, ""
        return real(cwd, *args, **kw)
    monkeypatch.setattr(sync, "_git", fake)
    code, msg = sync.run()
    assert code == 0, msg
    assert pushed == [("push", "--quiet", "origin", "team/test.one")]
    work = os.path.join(home, "team-data")
    head = subprocess.run(["git", "-C", work, "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    files = subprocess.run(["git", "-C", work, "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True).stdout.split()
    assert head == "team/test.one" and len(files) == 1 and files[0].startswith("members/test.one/")


def test_a_bad_handle_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "h"))
    _settings(str(tmp_path / "h"), enabled=True, repo="acme/team-data", handle="../main")
    assert sync.run(dry_run=True)[0] == 1
