"""The live panel follows its own session, counts subagents while they run, keeps cache re-reads apart, shows the
memories pulled for the latest request, and the end-of-turn line and the cross-session ledger agree with it.

Fixtures follow the record shapes Claude Code 2.1 writes: subagent transcripts under <session>/subagents with a
.meta.json naming the launching tool call, system turn_duration records at the end of a turn, and hook context in
attachment records. Every string is synthetic.
"""
import io, json, os, subprocess, sys, time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from nodaris_harness import dispatch, events, launch, monitor, statusline, tui, usage  # noqa: E402
from test_monitor import NOW, SECRET_BODY, assistant, iso, memory_attachment, result, write  # noqa: E402


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    for k in ("NO_COLOR", "NODARIS_REDUCED_MOTION", "NODARIS_PANEL_LINK", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"):
        monkeypatch.delenv(k, raising=False)


def prompt(ts, text="Add paging to the export"):
    return {"type": "user", "timestamp": iso(ts), "isSidechain": False, "message": {"role": "user", "content": text}}


def turn_end(ts):
    return {"type": "system", "subtype": "turn_duration", "durationMs": 1000, "timestamp": iso(ts)}


def session_dir(tmp_path, sid="s1"):
    d = tmp_path / "claude" / "projects" / "-repo"
    (d / sid / "subagents").mkdir(parents=True, exist_ok=True)
    return d


def launch_agent(ts, tid, desc="Map the repo"):
    return assistant("ma-" + tid, ts, 0, 0, 0, 0,
                     tools=[{"id": tid, "name": "Agent", "input": {"description": desc, "subagent_type": "Explore",
                                                                   "prompt": SECRET_BODY, "run_in_background": True}}])


def sub_rows(ts, n, tool_path="/repo/src/export.py"):
    rows = []
    for i in range(n):
        rows.append({"type": "assistant", "isSidechain": True, "timestamp": iso(ts + i),
                     "message": {"id": f"sub-{ts}-{i}", "role": "assistant",
                                 "content": [{"type": "tool_use", "id": f"st{i}", "name": "Read",
                                              "input": {"file_path": tool_path}}],
                                 "usage": {"input_tokens": 100, "cache_creation_input_tokens": 900,
                                           "cache_read_input_tokens": 20000, "output_tokens": 50}}})
    return rows


# ---- which session -------------------------------------------------------------------------------------------------

def test_a_linked_panel_follows_its_own_session_and_never_the_newest_in_the_folder(tmp_path):
    d = session_dir(tmp_path)
    mine, other = d / "mine.jsonl", d / "desktop.jsonl"
    write(mine, [prompt(NOW - 60), assistant("m1", NOW - 50, out=10)])
    write(other, [prompt(NOW - 5), assistant("o1", NOW - 4, read=70_000_000, out=999)])
    os.utime(other, None)
    assert monitor.find_transcript("/repo", root=str(tmp_path / "claude" / "projects")).endswith("desktop.jsonl")
    assert monitor.write_link("0123456789abcdef", str(mine), "mine", "/repo")
    args = type("A", (), {"split": False, "session": None, "follow": False, "host": "claude", "link": "0123456789abcdef"})()
    out = io.StringIO()
    assert monitor.watch(args, out) == 0
    assert "mine.jsonl" in out.getvalue() and "desktop.jsonl" not in out.getvalue()


@pytest.mark.parametrize("bad", ["../../etc/x", "ABC", "", None, "0123"])
def test_a_link_that_is_not_a_plain_hex_id_is_refused(bad):
    assert monitor.write_link(bad, "/t.jsonl", "s", "/repo") is False


def test_session_start_records_the_link_only_when_nodaris_started_the_session(tmp_path, monkeypatch):
    d = session_dir(tmp_path)
    t = str(d / "abc.jsonl")
    base = {"session_id": "abc", "cwd": "/repo", "transcript_path": t, "hook_event_name": "SessionStart", "source": "startup"}
    dispatch.handle(events.parse("claude", base))
    assert not os.path.exists(monitor.link_path("00ff00ff00ff00ff"))
    monkeypatch.setenv("NODARIS_PANEL_LINK", "00ff00ff00ff00ff")
    dispatch.handle(events.parse("claude", dict(base, source="clear")))
    assert monitor.read_link("00ff00ff00ff00ff") == t


# ---- what the numbers mean -----------------------------------------------------------------------------------------

def test_cache_re_reads_are_reported_apart_from_tokens_used():
    s = monitor.SessionStats()
    s.feed([prompt(NOW - 100), assistant("a", NOW - 90, inp=10, create=1000, read=5_000_000, out=200)])
    t = s.totals()
    assert t["used"] == 1210 and t["cache_read"] == 5_000_000 and monitor.billable(t) == 1210
    frame = tui.strip("\n".join(monitor.render_frame(s, 60, 80, now=NOW, mode="none", path="/x/s.jsonl")))
    assert "1.2k used" in frame and "5.0M cache re-reads" in frame


def test_this_request_counts_only_since_the_last_prompt_and_the_context_gauge_uses_the_latest_call():
    s = monitor.SessionStats()
    s.feed([prompt(NOW - 300), assistant("a", NOW - 290, inp=5, create=100, read=1000, out=50),
            prompt(NOW - 60), assistant("b", NOW - 50, inp=7, create=300, read=150_000, out=40),
            assistant("c", NOW - 40, inp=3, create=200, read=150_300, out=60)])
    r = s.request()
    assert (r["used"], r["calls"], r["start"]) == (7 + 300 + 40 + 3 + 200 + 60, 2, pytest.approx(NOW - 60))
    assert s.context == 3 + 200 + 150_300 and s.window() == 200_000
    s.model = "claude-opus-5-5[1m]"
    assert s.window() == 1_000_000


def test_a_notification_or_command_echo_is_not_a_new_request():
    s = monitor.SessionStats()
    s.feed([prompt(NOW - 100), prompt(NOW - 50, "<task-notification>\n<task-id>x</task-id>\n</task-notification>"),
            prompt(NOW - 40, "<command-name>/clear</command-name>"),
            {"type": "user", "timestamp": iso(NOW - 30), "isMeta": True, "message": {"role": "user", "content": "meta"}}])
    assert s.prompts == [pytest.approx(NOW - 100)]


# ---- subagents, live ------------------------------------------------------------------------------------------------

def test_a_running_subagent_shows_its_tokens_and_current_tool_before_it_finishes(tmp_path):
    d = session_dir(tmp_path)
    main = d / "s1.jsonl"
    write(main, [prompt(NOW - 120), launch_agent(NOW - 110, "tu9"),
                 result("tu9", NOW - 109, "launched", tur={"isAsync": True, "status": "async_launched", "agentId": "a9"})])
    sub = d / "s1" / "subagents" / "agent-a9.jsonl"
    (d / "s1" / "subagents" / "agent-a9.meta.json").write_text(json.dumps({"toolUseId": "tu9", "description": "Map the repo",
                                                                            "agentType": "Explore"}))
    write(sub, sub_rows(NOW - 100, 2))
    src = monitor.Source(str(main))
    s = src.poll(now=NOW)
    ag = s.agents["tu9"]
    assert ag["status"] == "background" and ag["used"] == 2 * 1050 and ag["cache"] == 40000
    assert ag["last"] == "Reading export.py"
    write(sub, sub_rows(NOW - 90, 3), mode="a")
    s = src.poll(now=NOW + 1)
    assert s.agents["tu9"]["used"] == 5 * 1050                          # grew while still running
    assert s.totals()["subagents"] == 5 * 1050 and s.totals()["cache_read"] == 100000
    frame = tui.strip("\n".join(monitor.render_frame(s, 70, 80, now=NOW, mode="none", path=str(main))))
    assert "Map the repo" in frame and "Reading export.py" in frame and "1 running" in frame
    assert "FILE-BODY" not in frame


def test_a_subagent_is_finished_when_its_own_transcript_ends_and_stalled_when_silent(tmp_path):
    d = session_dir(tmp_path)
    main = d / "s1.jsonl"
    write(main, [prompt(NOW - 4000)])
    for aid, tid in (("w1", "workflow-call"), ("w2", "workflow-call")):
        (d / "s1" / "subagents" / f"agent-{aid}.meta.json").write_text(json.dumps({"toolUseId": tid, "description": aid}))
    done = sub_rows(NOW - 3000, 2)
    done[-1]["message"]["stop_reason"] = "end_turn"
    write(d / "s1" / "subagents" / "agent-w1.jsonl", done)
    write(d / "s1" / "subagents" / "agent-w2.jsonl", sub_rows(NOW - 3000, 1))
    s = monitor.Source(str(main)).poll(now=NOW)
    assert s.agents["agent:w1"]["status"] == "completed" and s.agents["agent:w1"]["ms"] == 1000
    assert s.agents["agent:w2"]["status"] == "running" and s.running_agents(NOW) == 0 and not s.working(NOW)
    frame = tui.strip("\n".join(monitor.render_frame(s, 70, 80, now=NOW, mode="none", path=str(main))))
    assert "stalled" in frame and "running" not in frame.split("Subagents")[1].split("\n")[0]


def test_a_compaction_summary_is_not_a_request():
    s = monitor.SessionStats()
    s.feed([prompt(NOW - 100), dict(prompt(NOW - 10, "This session is being continued from a previous conversation"),
                                    isCompactSummary=True, isVisibleInTranscriptOnly=True)])
    assert s.prompts == [pytest.approx(NOW - 100)]


def test_a_nested_subagent_without_a_launch_in_the_main_transcript_still_counts(tmp_path):
    d = session_dir(tmp_path)
    main = d / "s1.jsonl"
    write(main, [prompt(NOW - 120)])
    (d / "s1" / "subagents" / "agent-n1.meta.json").write_text(json.dumps({"toolUseId": "elsewhere", "description": "Deep check"}))
    write(d / "s1" / "subagents" / "agent-n1.jsonl", sub_rows(NOW - 100, 1))
    s = monitor.Source(str(main)).poll(now=NOW)
    assert s.agents["agent:n1"]["name"] == "Deep check" and s.totals()["subagents"] == 1050


def test_a_finished_subagent_is_read_from_its_transcript_once_for_exact_figures(tmp_path):
    d = session_dir(tmp_path)
    main = d / "s1.jsonl"
    write(main, [prompt(NOW - 120), launch_agent(NOW - 110, "tu5", "Review"),
                 result("tu5", NOW - 60, "done", tur={"status": "completed", "agentId": "a5", "totalTokens": 999999,
                                                      "totalDurationMs": 50000})])
    write(d / "s1" / "subagents" / "agent-a5.jsonl", sub_rows(NOW - 100, 4))
    stats, complete = statusline.session_stats(str(main), "s1")
    assert complete and stats.agents["tu5"]["used"] == 4 * 1050 and stats.agents["tu5"]["cache"] == 80000
    assert stats.request()["used"] == 4 * 1050                        # the request figure includes its subagents


# ---- the timeline and motion ----------------------------------------------------------------------------------------

def test_the_token_flow_places_tokens_by_time_and_marks_prompts():
    s = monitor.SessionStats()
    s.feed([prompt(NOW - 580), assistant("a", NOW - 570, inp=0, create=0, read=9_000_000, out=100),
            prompt(NOW - 30), assistant("b", NOW - 20, inp=0, create=5000, read=0, out=1000)])
    vals = s.flow(NOW, 60)
    assert vals[3] == 100 and vals[58] == 6000 and sum(vals) == 6100          # cache re-reads never enter the flow
    rows, peak = monitor.flow_lines(s, monitor.Canvas("none"), 60, NOW, 0.0)
    assert peak == "6.0k" and rows[3].count("▲") == 2 and rows[-1].startswith("-10m") and rows[-1].endswith("now")
    assert rows[2][58] != " " and rows[2][3] != " " and rows[0][3] == " "


def test_numbers_count_up_and_announce_the_increase():
    m = monitor.Motion()
    assert m.value("used", 1000, NOW) == 1000
    shown = m.value("used", 11000, NOW + 0.1)
    assert 1000 < shown < 11000
    for i in range(40):
        shown = m.value("used", 11000, NOW + 0.2 + i * 0.1)
    assert shown == 11000
    amount, fade = m.recent("used", NOW + 1.0)
    assert amount == 10000 and 0 < fade < 1
    assert m.recent("used", NOW + 10) is None


def test_the_activity_line_tells_what_is_happening_now():
    s = monitor.SessionStats()
    s.feed([prompt(NOW - 30)])
    assert s.activity[0] == "thinking" and s.working(NOW)
    s.feed([assistant("a", NOW - 20, tools=[{"id": "b1", "name": "Bash", "input": {"command": "pytest -q tests/x.py"}}])])
    assert s.activity[:2] == ["tool", "Running pytest -q"]
    s.feed([result("b1", NOW - 10, "ok"), turn_end(NOW - 5)])
    assert s.activity[0] == "idle" and not s.working(NOW)
    line = tui.strip(monitor.activity_line(s, monitor.Canvas("none"), NOW, 0.0))
    assert "Waiting for you" in line


# ---- memories -----------------------------------------------------------------------------------------------------

def lesson_context(ts, text):
    return {"type": "attachment", "timestamp": iso(ts),
            "attachment": {"type": "hook_additional_context", "content": [text], "hookName": "UserPromptSubmit"}}


def test_memories_are_grouped_by_request_in_every_recall_format():
    harness = ("Lessons recorded from earlier work that match this request:\n"
               "- When adding an X12 835 parser path: wrap a fragment " + SECRET_BODY + " (matched: 835, envelope)")
    card = "### Repo card: app\n- Verify: `pytest`\n- Lesson: **Pin the test clock** " + SECRET_BODY
    s = monitor.SessionStats()
    s.feed([prompt(NOW - 200), memory_attachment(NOW - 199, ["old-memory"]),
            prompt(NOW - 100), lesson_context(NOW - 99, harness), lesson_context(NOW - 98, card),
            lesson_context(NOW - 90, "Lessons recorded for parsers/era.py:\n- When editing the ERA reader: keep CAS pairs")])
    items = s.recalls[-1]["items"]
    assert items == [["lesson", "adding an X12 835 parser path", "matched 835, envelope"],
                     ["repo card", "Pin the test clock", ""], ["lesson", "editing the ERA reader", "for era.py"]]
    frame = tui.strip("\n".join(monitor.render_frame(s, 70, 90, now=NOW, mode="none", path="/x/s.jsonl")))
    assert "For your last request" in frame and "old-memory" not in frame and "FILE-BODY" not in frame


# ---- the end-of-turn line -----------------------------------------------------------------------------------------

def test_the_end_of_turn_line_reaches_claude_code_as_a_system_message(tmp_path):
    d = session_dir(tmp_path)
    t = d / "s1.jsonl"
    write(t, [prompt(time.time() - 60), assistant("a", time.time() - 50, inp=10, create=1000, read=90_000, out=200)])
    ev = events.parse("claude", {"session_id": "s1", "cwd": str(tmp_path), "transcript_path": str(t),
                                 "hook_event_name": "Stop"})
    text = statusline.turn_notice(ev)
    assert text == "Token use: 1.2k for this request, 1.2k this session (plus 90k cache re-reads), 1.2k today across 1 session."
    out, code = events.render("claude", ev, {"decision": "allow", "notice": text})
    assert code == 0 and json.loads(out) == {"systemMessage": text}
    assert events.render("claude", ev, {"decision": "block", "reason": "run the tests", "notice": text})[0] == \
        json.dumps({"decision": "block", "reason": "run the tests"})
    assert events.render("codex", dict(ev, host="codex"), {"decision": "allow", "notice": text}) == ("", 0)


def test_the_end_of_turn_line_can_be_switched_off_and_never_fails(tmp_path):
    home = os.environ["NODARIS_HARNESS_HOME"]
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "settings.json"), "w") as fh:
        json.dump({"turn_summary": False}, fh)
    d = session_dir(tmp_path)
    write(d / "s1.jsonl", [prompt(NOW - 60)])
    ev = {"host": "claude", "session_id": "s1", "transcript_path": str(d / "s1.jsonl")}
    assert statusline.turn_notice(ev) == ""
    assert statusline.turn_notice({"host": "claude", "session_id": "x", "transcript_path": "/nope"}) == ""


# ---- across sessions ----------------------------------------------------------------------------------------------

def test_the_ledger_counts_each_message_once_across_lines_files_and_updates(tmp_path):
    root = tmp_path / "claude" / "projects"
    d = session_dir(tmp_path, "s1")
    now = time.time()
    a = assistant("m1", now - 100, inp=10, create=100, read=5000, out=20)
    write(d / "s1.jsonl", [a, a, assistant("m2", now - 90, inp=1, create=0, read=0, out=9)])
    write(d / "s2.jsonl", [a])                                             # a resumed copy of the same message
    write(d / "s1" / "subagents" / "agent-x.jsonl", sub_rows(now - 80, 1))
    led = usage.Ledger()
    assert led.update(str(root), now=now)
    today = led.summary(now)["today"]
    # the copy's message is counted once; the resumed session itself still counts as a session that ran today
    assert today == {"used": 130 + 10 + 1050, "cache_read": 5000 + 20000, "output": 20 + 9 + 50, "sessions": 2}
    write(d / "s1.jsonl", [assistant("m3", now - 10, inp=0, create=0, read=0, out=5)], mode="a")
    led.update(str(root), now=now)
    assert led.summary(now)["today"]["used"] == 130 + 10 + 1050 + 5
    led.save(usage.ledger_path())
    again = usage.Ledger.load(usage.ledger_path())
    again.update(str(root), now=now)
    assert again.summary(now)["today"]["used"] == 130 + 10 + 1050 + 5
    assert oct(os.stat(usage.ledger_path()).st_mode & 0o777) == "0o600"


def test_the_ledger_forgets_days_outside_the_window_and_holds_no_text(tmp_path):
    root = tmp_path / "claude" / "projects"
    d = session_dir(tmp_path, "s1")
    now = time.time()
    write(d / "s1.jsonl", [assistant("old", now - 20 * 86400, out=7), assistant("new", now - 60, out=8)])
    led = usage.refresh(None, root=str(root), now=now)
    assert all(day >= usage.day_of(now - 7 * 86400) for day in led.days)
    raw = open(usage.ledger_path()).read()
    assert "working" not in raw and "FILE-BODY" not in raw
    text = usage.report(led, now)
    assert text.startswith("Today: ") and "Last 7 days: " in text


def test_the_usage_command_prints_today_and_the_week(tmp_path, capsys):
    root = tmp_path / "claude" / "projects"
    d = session_dir(tmp_path, "s1")
    write(d / "s1.jsonl", [assistant("m", time.time() - 30, out=12)])
    env = dict(os.environ)
    r = subprocess.run([sys.executable, "-m", "nodaris_harness", "usage"], capture_output=True, text=True, env=env,
                       cwd=os.path.dirname(HERE), timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("Today: ") and "session(s)" in r.stdout
