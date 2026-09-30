"""The token monitor and status line: totals, incremental tailing, subagents, memories, burn, budgets and output hygiene.

Fixtures follow the record shapes Claude Code writes (assistant message.usage and tool_use blocks, user tool_result
blocks with toolUseResult, attachment records with hook_additional_context and task_status) and Codex's event_msg
token_count records. Every fixture string is synthetic.
"""
import io, json, os, sys, time
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "engine"))
from nodaris_harness import monitor, statusline, tui  # noqa: E402

SECRET_BODY = "FILE-BODY-SHOULD-NEVER-APPEAR " * 40
NOW = 1_800_000_000.0


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def assistant(mid, ts, inp=10, create=100, read=1000, out=50, tools=()):
    content = [{"type": "text", "text": "working"}] + [dict(type="tool_use", **t) for t in tools]
    return {"type": "assistant", "timestamp": iso(ts), "isSidechain": False, "requestId": "req-" + mid,
            "message": {"id": mid, "role": "assistant", "content": content,
                        "usage": {"input_tokens": inp, "cache_creation_input_tokens": create,
                                  "cache_read_input_tokens": read, "output_tokens": out}}}


def result(tid, ts, content=SECRET_BODY, tur=None, is_error=False):
    r = {"type": "user", "timestamp": iso(ts), "isSidechain": False,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content,
                                                  "is_error": is_error}]}}
    if tur is not None:
        r["toolUseResult"] = tur
    return r


def memory_attachment(ts, titles):
    body = "Memory (recalled for this task)\n" + "\n".join(f"- **{t}:** the lesson body {SECRET_BODY}" for t in titles)
    return {"type": "attachment", "timestamp": iso(ts),
            "attachment": {"type": "hook_additional_context", "content": [body], "hookName": "UserPromptSubmit",
                           "toolUseID": "x", "hookEvent": "UserPromptSubmit"}}


def session_rows(t0=NOW - 120):
    return [
        {"type": "custom-title", "customTitle": "t", "sessionId": "s1"},
        memory_attachment(t0, ["never-push-to-main", "2026-09-01: Verify in a clean checkout"]),
        assistant("m1", t0, inp=5, create=2000, read=20000, out=100,
                  tools=[{"id": "tu1", "name": "Read", "input": {"file_path": "/repo/src/app.py"}},
                         {"id": "tu2", "name": "Bash", "input": {"command": "cd /repo && API_KEY=abc123secretvalue9 pytest -q tests/x.py", "description": "run"}}]),
        assistant("m1", t0, inp=5, create=2000, read=20000, out=100),          # same message id: counted once
        result("tu1", t0 + 1, SECRET_BODY),
        result("tu2", t0 + 1, [{"type": "text", "text": "x" * 400}]),
        assistant("m2", t0 + 30, inp=7, create=300, read=22000, out=200,
                  tools=[{"id": "tu3", "name": "Edit", "input": {"file_path": "/repo/src/app.py", "old_string": SECRET_BODY, "new_string": SECRET_BODY}},
                         {"id": "tu4", "name": "Agent", "input": {"description": "Review the diff", "subagent_type": "reviewer", "prompt": SECRET_BODY}},
                         {"id": "tu5", "name": "Agent", "input": {"description": "Map the repo", "subagent_type": "Explore", "prompt": SECRET_BODY, "run_in_background": True}}]),
        result("tu3", t0 + 31, "ok"),
        result("tu4", t0 + 60, [{"type": "text", "text": SECRET_BODY}],
               tur={"status": "completed", "agentType": "reviewer", "totalTokens": 45000, "totalDurationMs": 90000,
                    "totalToolUseCount": 4, "prompt": SECRET_BODY, "content": [{"type": "text", "text": SECRET_BODY}]}),
        result("tu5", t0 + 61, "launched", tur={"isAsync": True, "status": "async_launched", "agentId": "a1",
                                                 "description": "Map the repo", "prompt": SECRET_BODY}),
    ]


def write(path, rows, mode="w"):
    with open(path, mode) as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    for k in ("NO_COLOR", "NODARIS_REDUCED_MOTION", "COLORTERM", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    if not os.environ.get("CLAUDE_CONFIG_DIR", "").startswith(str(tmp_path)):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def stats_for(rows):
    s = monitor.SessionStats()
    s.feed(rows)
    return s


def test_totals_and_split():
    s = stats_for(session_rows())
    t = s.totals()
    assert t["cache_read"] == 42000
    assert t["new_input"] == 5 + 2000 + 7 + 300
    assert t["output"] == 300
    assert t["subagents"] == 45000
    assert t["total"] == 42000 + 2312 + 300 + 45000
    assert s.turns == 2 and s.floor == 20000


def test_where_tokens_went_ranks_files_agents_and_replies():
    s = stats_for(session_rows())
    rows = dict(s.where(cwd="/repo"))
    assert rows["Agent Review the diff"] == 45000
    assert rows["Read src/app.py"] == len(SECRET_BODY) // 4
    assert rows["Shell pytest -q"] == 100
    assert rows["Replies written"] == 300
    assert list(rows)[0] == "Agent Review the diff"
    assert not any("cache" in k.lower() or "floor" in k.lower() for k in rows)     # re-reads are reported apart


def test_bash_command_is_shortened_without_secrets():
    assert monitor.short_command("cd /x && API_KEY=abc123secretvalue9 pytest -q tests/x.py") == "pytest -q"
    assert monitor.short_command("curl -H 'Authorization: Bearer sk-abc' https://x") == "curl -H"
    assert monitor.short_command("git push origin ghp_0123456789abcdefABCDEF") == "git push origin"
    assert "secret" not in monitor.short_command("echo sk1234567890abcdefghij | pbcopy")


def test_files_read_and_written():
    s = stats_for(session_rows())
    assert s.recent_files() == [("/repo/src/app.py", "written")]


def test_incremental_tailing_parses_only_new_lines(tmp_path):
    p = tmp_path / "s.jsonl"
    rows = session_rows()
    write(p, rows)
    tail = monitor.Tail(str(p))
    first = tail.read_new()
    assert len(first) == len(rows) and tail.lines_parsed == len(rows)
    assert tail.read_new() == [] and tail.lines_parsed == len(rows)
    write(p, [assistant("m3", NOW, read=1)], mode="a")
    with open(p, "a") as fh:
        fh.write('{"type": "assistant", "partial')                        # half-written line waits
    new = tail.read_new()
    assert len(new) == 1 and tail.lines_parsed == len(rows) + 1
    with open(p, "a") as fh:
        fh.write('": 1}\n')
    assert len(tail.read_new()) == 1


def test_subagent_rows_from_tool_use_result_and_notification():
    rows = session_rows()
    s = stats_for(rows)
    a = s.agents["tu4"]
    assert (a["name"], a["status"], a["tokens"], a["ms"], a["type"]) == ("Review the diff", "completed", 45000, 90000, "reviewer")
    assert s.agents["tu5"]["status"] == "background" and s.running_agents() == 1
    note = ("<task-notification>\n<task-id>a1</task-id>\n<tool-use-id>tu5</tool-use-id>\n<status>completed</status>\n"
            "<summary>done</summary>\n<usage><total_tokens>12000</total_tokens><tool_uses>3</tool_uses>"
            "<duration_ms>30000</duration_ms></usage>\n</task-notification>")
    s.feed([{"type": "user", "timestamp": iso(NOW - 10), "message": {"role": "user", "content": note}}])
    assert s.agents["tu5"]["status"] == "completed" and s.agents["tu5"]["tokens"] == 12000
    assert s.running_agents() == 0


def test_memory_titles_extracted_without_bodies():
    s = stats_for(session_rows())
    assert s.memories == ["never-push-to-main", "Verify in a clean checkout"]


def test_burn_rate_window():
    t0 = NOW
    s = stats_for([assistant("old", t0 - 600, 0, 1_000_000, 0, 0), assistant("new", t0 - 60, 0, 40_000, 9_000_000, 10_000)])
    assert s.burn_per_min(now=t0) == pytest.approx(10_000)          # cache re-reads never count as burn
    assert s.burn_per_min(now=t0 + 400) == 0


def test_budget_thresholds():
    assert monitor.level(0.69) == "ok" and monitor.level(0.70) == "warn" and monitor.level(0.9) == "high"
    b = monitor.budgets({"plan": "pro", "subagent_budget_tokens": 50000})
    assert b == {"plan": "pro", "session": monitor.SESSION_BUDGETS["pro"], "agents": 50000}
    assert monitor.level_rgb(0.95) == monitor.RED and monitor.level_rgb(0.75) == monitor.AMBER


def test_budget_reads_settings(tmp_path):
    home = os.environ["NODARIS_HARNESS_HOME"]
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "settings.json"), "w") as fh:
        json.dump({"plan": "team", "subagent_budget_tokens": 123456}, fh)
    assert monitor.budgets() == {"plan": "team", "session": monitor.SESSION_BUDGETS["team"], "agents": 123456}


def test_gate_stops_from_signals_log(tmp_path):
    src_path = tmp_path / "abc.jsonl"
    write(src_path, session_rows())
    sig = monitor.signals_path("abc")
    os.makedirs(os.path.dirname(sig), exist_ok=True)
    write(sig, [{"ts": NOW, "session": "x", "kind": "gate", "rule": "secret-scan"}])
    s = monitor.Source(str(src_path)).poll()
    assert s.gates == ["secret-scan"]


def test_codex_token_count():
    rows = [{"timestamp": iso(NOW), "type": "session_meta", "payload": {"id": "c", "cwd": "/repo"}},
            {"timestamp": iso(NOW), "type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 1000, "cached_input_tokens": 800, "cache_write_input_tokens": 0,
                                     "output_tokens": 40, "reasoning_output_tokens": 10, "total_tokens": 1040}}}}] * 2
    t = stats_for(rows).totals()
    assert (t["cache_read"], t["new_input"], t["output"]) == (1600, 400, 80)


def test_plain_output_has_no_escape_codes_or_contents():
    s = stats_for(session_rows())
    text = monitor.render_plain(s, cwd="/repo", now=NOW)
    assert "\x1b" not in text
    assert "FILE-BODY" not in text and "abc123secret" not in text
    assert "Where the tokens went:" in text and "never-push-to-main" in text


def test_watch_plain_mode_prints_once_and_exits(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    d = monitor.project_dir("/repo/work", str(root))
    os.makedirs(d)
    write(os.path.join(d, "s1.jsonl"), session_rows())
    assert monitor.find_transcript("/repo/work", root=str(root)).endswith("s1.jsonl")
    monkeypatch.setattr(monitor, "find_transcript", lambda *a, **k: os.path.join(d, "s1.jsonl"))
    out = io.StringIO()
    args = type("A", (), {"split": False, "session": None, "follow": False, "host": "claude"})()
    assert monitor.watch(args, out) == 0
    assert "\x1b" not in out.getvalue() and "Tokens used this session" in out.getvalue()


@pytest.mark.parametrize("cols", [120, 60])
def test_live_frame_fits_width_and_hides_contents(cols):
    s = stats_for(session_rows())
    for t in (0.0, 0.3, 1.7):
        lines = monitor.render_frame(s, cols, 40, t=t, mode="256", cwd="/repo", now=NOW)
        assert all(tui.visible_len(l) <= cols for l in lines)
        joined = tui.strip("\n".join(lines))
        assert "FILE-BODY" not in joined and "abc123secret" not in joined
        assert "Where the tokens went" in joined and "Subagents" in joined and "Files" in joined
    assert len(monitor.render_frame(s, 60, 10, now=NOW)) <= 10


def test_heat_scale():
    assert monitor.heat(0) == 0 and monitor.heat(10_000_000) == 1.0


def test_split_outside_tmux_explains_in_one_sentence(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    out = io.StringIO()
    args = type("A", (), {"session": None, "open": False})()
    assert monitor.split(args, out) == 0
    assert out.getvalue().count("\n") == 1 and "watch" in out.getvalue()


def _big_fixture(path, n=5000):
    rows, t0 = [], NOW - 3000
    for i in range(n // 2):
        rows.append(assistant(f"m{i}", t0 + i, tools=[{"id": f"t{i}", "name": "Read", "input": {"file_path": f"/repo/f{i % 50}.py"}}]))
        rows.append(result(f"t{i}", t0 + i, "x" * 200))
    rows.insert(1, memory_attachment(t0, ["a-long-memory-title-that-keeps-going-on-and-on"]))
    write(path, rows)


def test_statusline_under_150ms_on_5000_lines(tmp_path):
    p = tmp_path / "big.jsonl"
    _big_fixture(p)
    payload = {"session_id": "big", "transcript_path": str(p), "model": {"display_name": "M"}, "workspace": {"current_dir": "/repo"}}
    start = time.perf_counter()
    first = statusline.line(payload, now=NOW, colour=False)
    cold = time.perf_counter() - start
    start = time.perf_counter()
    again = statusline.line(payload, now=NOW, colour=False)
    warm = time.perf_counter() - start
    assert cold < 0.15, cold
    assert warm < 0.05, warm
    assert first == again and "tok" in first and "\x1b" not in first
    assert "mem a-long-memory-title" in first


def test_statusline_colour_and_no_color(tmp_path, monkeypatch):
    p = tmp_path / "s.jsonl"
    write(p, session_rows())
    payload = json.dumps({"session_id": "s", "transcript_path": str(p)})
    out = io.StringIO()
    statusline.main(stdin=io.StringIO(payload), out=out)
    assert "\x1b[" in out.getvalue() and out.getvalue().count("\n") == 1
    monkeypatch.setenv("NO_COLOR", "1")
    out = io.StringIO()
    statusline.main(stdin=io.StringIO(payload), out=out)
    text = out.getvalue()
    assert "\x1b" not in text and "FILE-BODY" not in text and "1 agent" in text


def test_statusline_bad_input_is_quiet():
    out = io.StringIO()
    assert statusline.main(stdin=io.StringIO("not json"), out=out) == 0
    assert out.getvalue().strip() == "nodaris: waiting for a session"


def test_monitor_never_writes_the_transcript(tmp_path):
    p = tmp_path / "s.jsonl"
    write(p, session_rows())
    before = (p.read_bytes(), os.path.getmtime(p))
    monitor.Source(str(p)).poll()
    statusline.line({"session_id": "s", "transcript_path": str(p)}, now=NOW)
    assert (p.read_bytes(), os.path.getmtime(p)) == before
