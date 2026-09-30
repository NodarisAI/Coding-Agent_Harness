"""The panel is about the session in front of the person: it counts from the moment the session was opened (a start,
a resume or a /clear, as Claude Code records it in the transcript), shows the whole conversation only as a second
line, keeps other sessions in their own block, and suggests the next Claude Code command from this session's own
context, cache and saved lessons.

Fixtures follow the record shapes Claude Code 2.1 writes: SessionStart hook results are attachment records whose
hookName is "SessionStart:<source>", and usage carries cache_creation.ephemeral_1h_input_tokens on a one-hour cache.
Every string is synthetic.
"""
import json, os, sys, time
from datetime import datetime

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from nodaris_harness import events, monitor, statusline, tui  # noqa: E402
from test_live_panel import launch_agent, prompt, session_dir, turn_end  # noqa: E402
from test_monitor import NOW, assistant, iso, result, write  # noqa: E402

DAY = 86400


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    for k in ("NO_COLOR", "NODARIS_REDUCED_MOTION", "NODARIS_PANEL_LINK", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"):
        monkeypatch.delenv(k, raising=False)


def opened(ts, source="resume"):
    return {"type": "attachment", "timestamp": iso(ts), "isSidechain": False,
            "attachment": {"type": "hook_success", "hookName": "SessionStart:" + source, "hookEvent": "SessionStart",
                           "content": "", "stdout": "", "exitCode": 0}}


def one_hour(row):
    row["message"]["usage"]["cache_creation"] = {"ephemeral_1h_input_tokens": row["message"]["usage"]
                                                 ["cache_creation_input_tokens"], "ephemeral_5m_input_tokens": 0}
    return row


def frame(s, now=NOW, cols=70, ledger=None):
    return tui.strip("\n".join(monitor.render_frame(s, cols, 200, now=now, mode="none", path="/x/s1.jsonl",
                                                    ledger=ledger)))


def hhmm(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def resumed_session():
    """Three days of conversation, then a resume an hour ago with two calls since."""
    return [opened(NOW - 3 * DAY, "startup"),
            prompt(NOW - 3 * DAY + 10), assistant("old1", NOW - 3 * DAY + 20, inp=5, create=4_000_000, read=9_000_000, out=500_000),
            opened(NOW - 2 * DAY, "compact"),
            assistant("old2", NOW - 2 * DAY + 20, inp=5, create=3_000_000, read=9_000_000, out=400_000),
            opened(NOW - 3600, "resume"), opened(NOW - 3600, "resume"),
            prompt(NOW - 3500), assistant("new1", NOW - 3490, inp=3, create=200_000, read=1_000_000, out=20_000),
            opened(NOW - 1800, "compact"),
            assistant("new2", NOW - 1700, inp=2, create=100_000, read=800_000, out=10_000), turn_end(NOW - 1690)]


# ---- scope ------------------------------------------------------------------------------------------------------------

def test_this_session_counts_from_the_last_open_and_a_compaction_does_not_restart_it():
    s = monitor.SessionStats()
    s.feed(resumed_session())
    assert s.opened == pytest.approx(NOW - 3600)
    since = s.scope()
    assert since == pytest.approx(NOW - 3600)
    now = s.totals(since)
    assert now["used"] == 3 + 200_000 + 20_000 + 2 + 100_000 + 10_000
    assert now["cache_write"] == 300_000 and now["cache_read"] == 1_800_000
    whole = s.totals()
    assert whole["used"] == now["used"] + 5 + 4_000_000 + 500_000 + 5 + 3_000_000 + 400_000


def test_a_session_opened_once_has_no_separate_whole_conversation_line():
    s = monitor.SessionStats()
    s.feed([opened(NOW - 600, "startup"), prompt(NOW - 590), assistant("a", NOW - 580, create=1000, out=200)])
    assert s.scope() is None
    text = frame(s)
    assert "since " + hhmm(NOW - 600) in text and "Whole conversation" not in text


def test_the_panel_leads_with_this_session_and_labels_the_whole_conversation():
    s = monitor.SessionStats()
    s.feed(resumed_session())
    text = frame(s)
    first = next(l for l in text.splitlines() if l.startswith("This session"))
    assert "330k" in first and "since " + hhmm(NOW - 3600) in first
    assert "300k written to cache" in text and "30k output" in text
    whole = next(l for l in text.splitlines() if l.startswith("Whole conversation"))
    assert "8.2M" in whole and "since " + datetime.fromtimestamp(NOW - 3 * DAY).strftime("%b %-d") in whole


def test_subagents_from_before_the_session_opened_are_not_this_sessions():
    s = monitor.SessionStats()
    rows = resumed_session()
    rows[2:2] = [launch_agent(NOW - 3 * DAY + 30, "old-agent", "Old research"),
                 result("old-agent", NOW - 3 * DAY + 60, tur={"status": "completed", "totalTokens": 900_000})]
    rows += [launch_agent(NOW - 600, "new-agent", "Map the export"),
             result("new-agent", NOW - 500, tur={"status": "completed", "totalTokens": 40_000})]
    s.feed(rows)
    assert s.totals(s.scope())["subagents"] == 40_000
    text = frame(s)
    assert "Map the export" in text and "Old research" not in text


def test_the_scoped_total_survives_the_status_line_cache_pruning_old_messages():
    s = monitor.SessionStats()
    s.feed(resumed_session())
    s.feed([assistant(f"m{i}", NOW - 1000 + i, inp=1, create=10, read=100, out=1) for i in range(30)])
    expected = s.totals(s.scope())
    again = monitor.SessionStats(json.loads(json.dumps(s.state(keep=5))))
    assert again.totals(again.scope()) == expected
    assert again.totals()["used"] == s.totals()["used"]


# ---- suggestions ------------------------------------------------------------------------------------------------------

def ctx_session(context, idle=30, lessons=0, ttl_hour=False, model="claude-opus-5-5", stalled=False):
    rows = [opened(NOW - 5000, "startup"), prompt(NOW - 4900)]
    if stalled:
        rows.append(launch_agent(NOW - 4000, "slow", "Scan the vault"))
    for i in range(lessons):
        tid = f"les{i}"
        rows.append(assistant("l" + tid, NOW - 700 + i, tools=[{"id": tid, "name": "Bash", "input": {
            "command": 'nodaris-harness lessons add --when "the export pages" --do "stream rows" --why "memory"'}}]))
        rows.append(result(tid, NOW - 699 + i, content="Added lesson L-1"))
    last = assistant("last", NOW - idle, inp=10, create=1000, read=context - 1010, out=100)
    last["message"]["model"] = model
    rows += [one_hour(last) if ttl_hour else last, turn_end(NOW - idle + 1)]
    s = monitor.SessionStats()
    s.feed(rows)
    if stalled:
        s.agents["slow"]["status"] = "background"
    return s


def texts(s, now=NOW):
    return " ".join(t for _, t in monitor.suggestions(s, now))


def test_a_healthy_session_says_nothing_needs_changing():
    s = ctx_session(40_000)
    assert monitor.suggestions(s, NOW) == []
    assert "Nothing to change" in frame(s)


def test_a_filling_context_suggests_compact_before_claude_code_does_it_for_you(monkeypatch):
    s = ctx_session(130_000)
    assert "/compact" in texts(s) and "65%" in texts(s)
    monkeypatch.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "45")
    s = ctx_session(80_000)
    t = texts(s)
    assert "/compact" in t and "45%" in t and "40%" in t


def test_an_idle_session_past_the_cache_lifetime_is_told_what_the_next_message_costs():
    s = ctx_session(90_000, idle=400)
    assert "cache expired" in texts(s) and "90k" in texts(s) and "/clear" in texts(s)
    s = ctx_session(90_000, idle=400, ttl_hour=True)
    assert s.ttl == 3600 and "expired" not in texts(s)
    s = ctx_session(90_000, idle=3000, ttl_hour=True)
    assert "expires in 10 min" in texts(s)


def test_saved_lessons_make_clear_safe_and_unsaved_work_is_flagged_before_clearing():
    s = ctx_session(110_000, lessons=2)
    assert s.saved == 2
    t = texts(s)
    assert "2 lessons were saved" in t and "/clear" in t
    t = texts(ctx_session(110_000))
    assert "Nothing from this session has been saved" in t


def test_a_stalled_subagent_is_raised():
    s = ctx_session(40_000, stalled=True)
    assert "Scan the vault" in texts(s) and "30 minutes" in texts(s)


def test_suggestions_are_capped_and_the_most_urgent_comes_first():
    s = ctx_session(190_000, idle=400, stalled=True)
    got = monitor.suggestions(s, NOW)
    assert len(got) == 3 and "/compact" in got[0][1]


# ---- other sessions and the app line --------------------------------------------------------------------------------

def test_other_sessions_have_their_own_block():
    s = ctx_session(40_000)
    ledger = {"today": {"used": 9_100_000, "cache_read": 0, "output": 0, "sessions": 4},
              "week": {"used": 60_000_000, "cache_read": 0, "output": 0, "sessions": 30}, "complete": True}
    text = frame(s, ledger=ledger)
    lines = text.splitlines()
    head = next(i for i, l in enumerate(lines) if l.startswith("All your sessions"))
    assert head > next(i for i, l in enumerate(lines) if l.startswith("This session"))
    assert "9.1M" in text and "4 sessions" in text


def test_the_end_of_turn_line_uses_this_session_and_adds_the_top_suggestion(tmp_path):
    d = session_dir(tmp_path)
    t = d / "s1.jsonl"
    now = time.time()
    write(t, [opened(now - 3 * DAY, "startup"), prompt(now - 3 * DAY + 5),
              assistant("old", now - 3 * DAY + 10, inp=5, create=5_000_000, read=1000, out=1000),
              opened(now - 600, "resume"), prompt(now - 60),
              assistant("a", now - 50, inp=10, create=1000, read=150_000, out=200)])
    ev = events.parse("claude", {"session_id": "s1", "cwd": str(tmp_path), "transcript_path": str(t),
                                 "hook_event_name": "Stop"})
    text = statusline.turn_notice(ev)
    assert text.startswith("Token use: 1.2k for this request, 1.2k this session since " + hhmm(now - 600))
    assert "(plus 150k cache re-reads)" in text and "5.0M" not in text.split("today")[0]
    assert "Suggestion: " in text and "/compact" in text


# ---- the offer at session start ------------------------------------------------------------------------------------

SID = "0d6e1c2a-5b7f-4c3e-9a10-2f4b8c6d7e91"


def start(tmp_path, source="startup", host="claude", sid=SID):
    from nodaris_harness import dispatch
    d = session_dir(tmp_path)
    ev = events.parse(host, {"session_id": sid, "cwd": "/repo", "transcript_path": str(d / (sid + ".jsonl")),
                             "hook_event_name": "SessionStart", "source": source})
    return dispatch.handle(ev).get("context") or ""


def test_the_desktop_app_is_offered_the_dashboard_in_its_terminal_panel(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    monkeypatch.delenv("TMUX", raising=False)
    ctx = start(tmp_path)
    assert "AskUserQuestion" in ctx and "run_in_terminal" in ctx
    assert "watch --host claude --session " + SID in ctx
    assert "watch --host claude --session " + SID in start(tmp_path, "resume")


def test_the_command_line_inside_tmux_opens_the_dashboard_beside_the_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,1,0")
    ctx = start(tmp_path)
    assert "tmux split-window -h" in ctx and SID in ctx
    monkeypatch.delenv("TMUX")
    ctx = start(tmp_path)
    assert "tmux split-window" not in ctx and "second terminal" in ctx and "nodaris" in ctx


def test_no_offer_when_the_panel_is_open_after_compaction_for_other_hosts_or_when_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    assert "token dashboard" not in start(tmp_path, "compact")
    assert "token dashboard" not in start(tmp_path, host="codex")
    monkeypatch.setenv("NODARIS_PANEL_LINK", "00ff00ff00ff00ff")
    assert "token dashboard" not in start(tmp_path)
    monkeypatch.delenv("NODARIS_PANEL_LINK")
    home = os.environ["NODARIS_HARNESS_HOME"]
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "settings.json"), "w") as fh:
        json.dump({"dashboard_offer": False}, fh)
    assert "token dashboard" not in start(tmp_path)


def test_a_session_id_that_is_not_a_plain_id_never_reaches_a_command(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    assert "token dashboard" not in start(tmp_path, sid="x; rm -rf ~")
    assert "token dashboard" not in start(tmp_path, sid="$(curl evil)")


def test_a_status_line_cache_from_before_session_scope_is_read_again_from_the_start(tmp_path):
    d = session_dir(tmp_path)
    t = d / "s1.jsonl"
    now = time.time()
    write(t, [opened(now - 3 * DAY, "startup"), assistant("old", now - 3 * DAY + 10, create=5_000_000, out=1000),
              opened(now - 600, "resume"), assistant("a", now - 50, inp=10, create=1000, read=150_000, out=200)])
    old = {"transcript": str(t), "offset": os.path.getsize(t), "sig": 0,
           "state": {"msgs": {"old": [now - 3 * DAY + 10, 10, 5_000_000, 1000, 1000]}, "order": ["old"]}}
    path = statusline._cache_path("s1")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(old, fh)
    stats, complete = statusline.session_stats(str(t), "s1")
    assert complete and stats.scope() == pytest.approx(now - 600, abs=1)
    assert stats.totals(stats.scope())["used"] == 1210
