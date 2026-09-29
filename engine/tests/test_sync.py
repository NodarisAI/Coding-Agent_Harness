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


def _git_env(monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sync, "GIT_ENV", dict(os.environ, GIT_TERMINAL_PROMPT="0"))


def _vault(tmp_path, pages=None):
    """A bare 'vault' remote with a main branch holding the given team pages."""
    remote = tmp_path / "vault.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "MAP.md").write_text("map\n")
    (seed / ".claude-plugin").mkdir()
    (seed / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "nodaris-context", "version": "1.0.14"}))
    (seed / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": [{"name": "nodaris-context", "version": "1.0.14"}]}))
    for rel, text in (pages or {}).items():
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_text(text)
    for cmd in (["add", "-A"], ["commit", "-qm", "seed"]):
        subprocess.run(["git", "-C", str(seed), *cmd], check=True)
    # Built by cloning, not pushing: the machine's push gate rightly refuses pushes to main, even to a temp folder.
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(remote)], check=True)
    return remote


def test_sync_pushes_pages_only_to_the_persons_own_vault_branch(tmp_path, monkeypatch):
    home = str(tmp_path / "h")
    monkeypatch.setenv("NODARIS_HARNESS_HOME", home)
    _git_env(monkeypatch)
    other = sync.lesson_page({"id": "abc123", "when": "When a claim export stalls", "do": "Page the query",
                              "keywords": ["export"], "verified": True, "created": "2026-09-01"}, "sam.lee")
    remote = _vault(tmp_path, {"team/sam.lee/lessons/abc123.md": other})
    _settings(home, enabled=True, repo="NodarisAI/Nodaris-Memory-Vault", handle="test.one", url=str(remote))
    memory.add(policy.home(), "When the export is slow", "Batch the query", scope="user")
    real, pushed = sync._git, []

    def fake(cwd, *args, **kw):
        if args[:1] == ("push",):
            pushed.append(args)
            return 0, ""
        return real(cwd, *args, **kw)
    monkeypatch.setattr(sync, "_git", fake)
    code, msg = sync.run()
    assert code == 0, msg
    assert pushed == [("push", "--quiet", "origin", "agent/team-memory/test.one")]
    work = os.path.join(home, "team-data")
    files = subprocess.run(["git", "-C", work, "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True).stdout.split()
    assert files and all(f.startswith("team/test.one/") for f in files)
    assert any(f.startswith("team/test.one/lessons/") and f.endswith(".md") for f in files)
    assert any(f.startswith("team/test.one/telemetry/") for f in files)
    # the rest of the team's lessons come back into recall; the person's own are not duplicated
    recalled = memory.load(str(tmp_path))
    assert any(l.get("member") == "sam.lee" and l["do"] == "Page the query" for l in recalled)
    assert "1 lesson(s) from the rest of the team" in msg
    assert sync.run()[1].startswith("Nothing new to share")


def test_a_lesson_page_round_trips():
    lesson = {"id": "0123abcd", "when": "When a 835 has no CLP", "do": "Refuse the file", "dont": "Guess the claim",
              "why": "The payment cannot be matched", "keywords": ["835", "clp"], "verified": True, "created": "2026-09-29"}
    back = sync.parse_page(sync.lesson_page(lesson, "jane.doe"))
    assert {k: back[k] for k in lesson} == lesson and back["member"] == "jane.doe"
    assert sync.parse_page("no front matter") is None
    assert sync.parse_page("---\nid: ../../etc\n---\n") is None


def test_intake_takes_only_well_formed_pages_and_bumps_the_plugin(tmp_path, monkeypatch):
    _git_env(monkeypatch)
    remote = _vault(tmp_path)
    member = tmp_path / "member"
    subprocess.run(["git", "clone", "-q", str(remote), str(member)], check=True)
    subprocess.run(["git", "-C", str(member), "switch", "-qc", "agent/team-memory/jane.doe"], check=True)
    page = sync.lesson_page({"id": "feed01", "when": "When retries loop", "do": "Cap them at three", "keywords": []}, "jane.doe")
    (member / "team" / "jane.doe" / "lessons").mkdir(parents=True)
    (member / "team" / "jane.doe" / "lessons" / "feed01.md").write_text(page)
    (member / "team" / "jane.doe" / "lessons" / "feed02.md").write_text(
        sync.lesson_page({"id": "feed02", "when": "Call 555-867-5309", "do": "x", "keywords": []}, "jane.doe"))
    (member / "team" / "sam.lee").mkdir(parents=True)
    (member / "team" / "sam.lee" / "notes.md").write_text("not jane's folder")
    for cmd in (["add", "-A"], ["commit", "-qm", "m"]):
        subprocess.run(["git", "-C", str(member), *cmd], check=True)
    subprocess.run(["git", "-C", str(remote), "fetch", "-q", str(member),
                    "agent/team-memory/jane.doe:agent/team-memory/jane.doe"], check=True)
    vault = tmp_path / "vault"
    subprocess.run(["git", "clone", "-q", str(remote), str(vault)], check=True)
    code, msg = sync.intake(str(vault), dry_run=True)
    assert code == 0 and "jane.doe: 1 file(s)" in msg and "feed02.md" in msg
    code, msg = sync.intake(str(vault), today="2026-09-29")
    assert code == 0, msg
    assert (vault / "team" / "jane.doe" / "lessons" / "feed01.md").exists()
    assert not (vault / "team" / "jane.doe" / "lessons" / "feed02.md").exists()
    assert not (vault / "team" / "sam.lee").exists()
    assert "jane.doe (1)" in (vault / "team" / "INDEX.md").read_text()
    assert json.loads((vault / ".claude-plugin" / "plugin.json").read_text())["version"] == "1.0.15"
    assert json.loads((vault / ".claude-plugin" / "marketplace.json").read_text())["plugins"][0]["version"] == "1.0.15"
    branch = subprocess.run(["git", "-C", str(vault), "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    assert branch == "chore/team-memory-2026-09-29"


def test_background_sync_runs_at_most_once_a_day(tmp_path, monkeypatch):
    home = str(tmp_path / "h")
    monkeypatch.setenv("NODARIS_HARNESS_HOME", home)
    monkeypatch.delenv("NODARIS_HARNESS_NO_BG", raising=False)
    _settings(home, enabled=True, repo="NodarisAI/Nodaris-Memory-Vault", handle="test.one")
    started = []
    monkeypatch.setattr(sync.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert sync.start_in_background("nodaris-harness") and not sync.start_in_background("nodaris-harness")
    assert len(started) == 1
    _settings(home, enabled=False)
    os.remove(sync._stamp())
    assert not sync.start_in_background("nodaris-harness")


def test_a_bad_handle_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "h"))
    _settings(str(tmp_path / "h"), enabled=True, repo="acme/team-data", handle="../main")
    assert sync.run(dry_run=True)[0] == 1


def test_lesson_keywords_are_redacted_and_odd_addresses_refused(tmp_path, monkeypatch):
    home = str(tmp_path / "h")
    monkeypatch.setenv("NODARIS_HARNESS_HOME", home)
    _settings(home, enabled=True, repo="acme/team-data", handle="test.one")
    memory.add(policy.home(), "When exporting", "Check the export", keywords=["555-867-5309", "export"], scope="user")
    code, text = sync.run(dry_run=True)
    assert code == 0 and "555-867-5309" not in text
    _settings(home, enabled=True, repo="acme/team-data", handle="test.one", url="--upload-pack=touch /tmp/x")
    assert sync.run()[0] == 1
