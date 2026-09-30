"""The dashboard follows the session it was opened for (Varun, 2026-09-30: every session runs in ai-os, and the panel
kept showing whichever session wrote last instead of the one in front of him). Session ids here are synthetic."""
import os, sys, time
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from nodaris_harness import monitor  # noqa: E402

MINE = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def projects(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    root = tmp_path / "claude" / "projects"
    monkeypatch.setattr(monitor, "claude_projects_root", lambda: str(root))
    d = root / monitor.re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath("/work/ai-os"))
    d.mkdir(parents=True)
    for i, sid in enumerate((MINE, OTHER)):
        p = d / (sid + ".jsonl")
        p.write_text("{}\n")
        os.utime(p, (time.time() + i, time.time() + i))   # OTHER is the newest
    return d


def args(**kw):
    return SimpleNamespace(host="claude", link=None, session=None, **kw)


def test_a_panel_started_by_a_session_shows_that_session_not_the_newest(projects):
    path, session, _ = monitor.resolve(args(), "/work/ai-os", env={"CLAUDE_CODE_SESSION_ID": MINE})
    assert path.endswith(MINE + ".jsonl") and session == MINE


def test_the_live_panel_never_jumps_to_a_busier_session(projects):
    mine = str(projects / (MINE + ".jsonl"))
    os.utime(projects / (OTHER + ".jsonl"), (time.time() + 60, time.time() + 60))
    assert monitor.next_path(mine, None, "/work/ai-os") == mine
    assert monitor.next_path(None, None, "/work/ai-os").endswith(OTHER + ".jsonl")   # nothing found yet: newest


def test_a_session_id_is_found_even_when_the_panel_runs_in_another_folder(projects):
    assert monitor.find_transcript("/somewhere/else", MINE).endswith(MINE + ".jsonl")
    assert monitor.find_transcript("/somewhere/else", "../" + MINE) is None


def test_the_desktop_app_session_id_gives_a_link_that_follows_clear(projects):
    env = {"CLAUDE_CODE_HOST_SESSION_ID": "local_fixture-app-session"}
    link = monitor.session_link(env)
    assert monitor.LINK_RE.match(link)
    monitor.write_link(link, str(projects / (MINE + ".jsonl")), MINE, "/work/ai-os")
    path, _, got = monitor.resolve(args(), "/work/ai-os", env=env)
    assert got == link and path.endswith(MINE + ".jsonl")
    monitor.write_link(link, str(projects / (OTHER + ".jsonl")), OTHER, "/work/ai-os")   # after /clear
    assert monitor.next_path(path, link, "/work/ai-os").endswith(OTHER + ".jsonl")


def test_the_launcher_link_wins_over_the_app_id():
    assert monitor.session_link({"NODARIS_PANEL_LINK": "abcdef0123456789", "CLAUDE_CODE_HOST_SESSION_ID": "x"}) == "abcdef0123456789"
    assert monitor.session_link({}) is None


def test_the_desktop_offer_opens_the_panel_on_its_own_session(monkeypatch):
    env = {"CLAUDE_CODE_ENTRYPOINT": "claude-desktop", "CLAUDE_CODE_HOST_SESSION_ID": "local_fixture-app-session"}
    ctx = monitor.dashboard_offer({"host": "claude", "source": "startup", "session_id": MINE}, "nodaris-harness", env)
    assert "--session " + MINE in ctx and "--link " + monitor.session_link(env) in ctx
