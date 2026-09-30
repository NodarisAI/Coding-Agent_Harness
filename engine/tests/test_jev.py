"""Jev: privacy before anything is sent, the team key from AWS, budgets and switches, and the setup checklist."""
import http.server, json, os, stat, sys, threading, types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import time  # noqa: E402

from nodaris_harness import dispatch, events, jev, onboard, setupcheck  # noqa: E402

KEY = "sk-or-v1-" + "a1" * 24
ANSWERS = {"answers": {"type": {"choice": "fix", "confidence": 0.9}, "effort": {"choice": "medium", "confidence": 0.8},
                       "shape_reply": {"choice": "brief", "probabilities": {"brief": 0.7}},
                       "critical": {"noul": 0.1}},
           "usage": {"cost": 0.0002}}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")
    monkeypatch.delenv("NODARIS_JEV", raising=False)
    (tmp_path / "user").mkdir()
    return tmp_path


@pytest.fixture
def server(monkeypatch):
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append({"auth": self.headers.get("Authorization"), "body": json.loads(body)})
            out = json.dumps(ANSWERS).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NODARIS_JEV_URL", "http://127.0.0.1:%d/api/alpha/decisions" % srv.server_port)
    yield seen
    srv.shutdown()


def put_key(chosen=True):
    os.makedirs(os.path.dirname(jev.key_path()), exist_ok=True)
    with open(jev.key_path(), "w") as fh:
        fh.write(KEY + "\n")
    with open(os.path.join(os.path.dirname(os.path.dirname(jev.key_path())), "settings.json"), "w") as fh:
        json.dump({"is_nodaris": True, "jev": chosen}, fh)


def ev(prompt, session="s1"):
    return {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": session, "cwd": os.getcwd()}


def test_no_key_adds_nothing_and_sends_nothing(server):
    assert jev.for_prompt(ev("fix the failing export button test in the billing module"), "fix the failing export button test in the billing module") == ""
    assert server == []


def test_ordinary_message_is_read_and_shaped(server):
    put_key()
    text = "fix the failing export button test in the billing module please"
    out = jev.for_prompt(ev(text), text)
    assert "Route: fix · effort medium" in out and "Reply shape: brief" in out
    assert server[0]["auth"] == "Bearer " + KEY
    assert server[0]["body"]["model"] == jev.MODEL and "reply" not in server[0]["body"]["questions"]
    assert KEY not in json.dumps(server[0]["body"])


def test_patient_identifiers_are_never_sent(server):
    put_key()
    text = "Patient John Smith, SSN 123-45-6789, DOB 03/14/1961, member ID ABC123456789 was denied, fix the claim"
    assert jev.for_prompt(ev(text), text) == ""
    assert server == []


def test_credentials_are_masked_before_sending(server):
    put_key()
    text = "the deploy fails with key sk-live-" + "Zz9" * 10 + " and " + "AKIA" + "ABCDEFGHIJKLMNOP" + ", why does it fail"
    jev.for_prompt(ev(text), text)
    sent = server[0]["body"]["state"]["message"]
    assert "sk-live-" not in sent and "AKIA" not in sent and "[SECRET-" in sent


def test_short_slash_subagent_and_off_are_skipped(server, monkeypatch):
    put_key()
    assert jev.for_prompt(ev("yes"), "yes") == ""
    assert jev.for_prompt(ev("/review the whole branch now please ok"), "/review the whole branch now please ok") == ""
    sub = dict(ev("x"), agent_id="a1")
    assert jev.for_prompt(sub, "explain how the router picks a playbook for a request") == ""
    jev.set_enabled(False)
    assert jev.for_prompt(ev("x"), "explain how the router picks a playbook for a request") == ""
    jev.set_enabled(True)
    monkeypatch.setenv("NODARIS_JEV", "off")
    assert jev.for_prompt(ev("x"), "explain how the router picks a playbook for a request") == ""
    assert server == []


def test_daily_cap_stops_calls(server, monkeypatch):
    put_key()
    monkeypatch.setattr(jev, "DAILY_CAP_USD", 0.0001)
    text = "fix the failing export button test in the billing module please"
    jev.for_prompt(ev(text), text)
    jev.for_prompt(ev(text), text)
    assert len(server) == 1


def test_network_failures_open_a_breaker(monkeypatch):
    put_key()
    monkeypatch.setenv("NODARIS_JEV_URL", "http://127.0.0.1:9/api/alpha/decisions")
    text = "fix the failing export button test in the billing module please"
    for _ in range(3):
        assert jev.for_prompt(ev(text), text) == ""
    assert jev._recent_failures() == 2


def test_prompt_hook_adds_jev_every_message_and_asks_reply_after_the_first(server):
    put_key()
    text = "explain how the redaction step decides what counts as a strong identifier"
    first = dispatch.prompt(events.parse("claude", ev(text), None))
    again = dispatch.prompt(events.parse("claude", ev(text), None))
    assert "Reply shape: brief" in first["context"] and "Reply shape: brief" in again["context"]
    assert "reply" not in server[0]["body"]["questions"] and "reply" in server[1]["body"]["questions"]


def runner_ok(value):
    def run(cmd, **kw):
        assert cmd[:3] == ["aws", "secretsmanager", "get-secret-value"] and jev.SECRET_ID in cmd
        return types.SimpleNamespace(returncode=0, stdout=value + "\n", stderr="")
    return run


def test_fetch_key_saves_a_private_file_and_never_echoes_it():
    ok, msg = jev.fetch_key(runner=runner_ok(KEY))
    assert ok and KEY not in msg
    assert open(jev.key_path()).read().strip() == KEY
    assert stat.S_IMODE(os.stat(jev.key_path()).st_mode) == 0o600


def test_fetch_key_reads_a_json_secret():
    ok, _ = jev.fetch_key(runner=runner_ok(json.dumps({"OPENROUTER_API_KEY": KEY})))
    assert ok and open(jev.key_path()).read().strip() == KEY


@pytest.mark.parametrize("stderr,expect", [
    ("An error occurred (AccessDeniedException) when calling GetSecretValue", "cannot read the team key"),
    ("An error occurred (ResourceNotFoundException)", "does not exist"),
    ("Unable to locate credentials. You can configure credentials by running aws configure.", "not signed in"),
])
def test_fetch_key_explains_aws_failures(stderr, expect):
    run = lambda cmd, **kw: types.SimpleNamespace(returncode=255, stdout="", stderr=stderr)
    ok, msg = jev.fetch_key(runner=run)
    assert not ok and expect in msg and not os.path.exists(jev.key_path())


def test_fetch_key_without_aws_cli():
    def run(cmd, **kw):
        raise FileNotFoundError("aws")
    ok, msg = jev.fetch_key(runner=run)
    assert not ok and "brew install awscli" in msg


def test_jev_is_only_for_the_nodaris_team():
    base = {"company": "Example Health", "use": ["general"], "hosts": ["claude"]}
    assert onboard.build(dict(base, jev=True))["jev"] is False
    assert onboard.build(dict(base, company="Nodaris"))["jev"] is True
    assert onboard.build(dict(base, company="Nodaris", jev=False))["jev"] is False


def test_setup_check_lists_what_is_missing_with_the_fix():
    def which(name):
        return "/usr/bin/" + name if name in ("claude", "gh", "git") else None

    def run(cmd, **kw):
        if cmd[:2] == ["claude", "--version"]:
            return types.SimpleNamespace(returncode=0, stdout="2.1.284 (Claude Code)", stderr="")
        if cmd[:3] == ["claude", "auth", "status"]:
            return types.SimpleNamespace(returncode=0, stdout='{"loggedIn": false}', stderr="")
        if cmd[0] == "git":
            return types.SimpleNamespace(returncode=0, stdout="Jane Doe" if "user.name" in cmd else "jane@nodaris.ai", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    items = setupcheck.checks({"is_nodaris": True, "hosts": ["claude"], "jev": True}, which=which, runner=run)
    by = {t: (ok, fix) for t, ok, _, fix in items}
    assert by["Claude Code command line"][0] and not by["Claude Code sign-in"][0]
    assert by["Git identity"][0]
    assert not by["GitHub sign-in"][0] and "gh auth login" in by["GitHub sign-in"][1]
    assert not by["Jev team key"][0] and "brew install awscli" in by["Jev team key"][1]
    assert "install --host git" in by["Git hooks in your repositories"][1]
    text = "\n".join(setupcheck.render(items))
    assert "item(s) left" in text


def test_the_key_goes_only_to_openrouter_or_a_loopback_test_server(monkeypatch):
    assert jev._endpoint("https://attacker.example/x") == jev.URL
    assert jev._endpoint("http://openrouter.ai/api/alpha/decisions") == jev.URL
    assert jev._endpoint("https://openrouter.ai.attacker.example/x") == jev.URL
    assert jev._endpoint("http://127.0.0.1:8123/x") == "http://127.0.0.1:8123/x"
    monkeypatch.setenv("NODARIS_JEV_URL", "https://attacker.example/x")
    assert jev._endpoint() == jev.URL


def test_onboarding_off_is_enforced_even_with_a_key(server):
    put_key(chosen=False)
    text = "fix the failing export button test in the billing module please"
    assert jev.for_prompt(ev(text), text) == "" and server == []


def test_names_without_a_cue_are_masked_before_sending(server):
    put_key()
    text = "Maria Gonzalez called about the denial, fix her appeal letter today"
    jev.for_prompt(ev(text), text)
    sent = server[0]["body"]["state"]["message"]
    assert "Maria" not in sent and "Gonzalez" not in sent and "[NAME]" in sent


def test_a_hung_server_costs_at_most_the_budget(monkeypatch):
    import socket
    put_key()
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)   # accepts the connection, never answers
    monkeypatch.setenv("NODARIS_JEV_URL", "http://127.0.0.1:%d/x" % lst.getsockname()[1])
    monkeypatch.setattr(jev, "BUDGET_S", 0.8)
    monkeypatch.setattr(jev, "ATTEMPT_S", 5.0)
    text = "fix the failing export button test in the billing module please"
    t0 = time.monotonic()
    assert jev.for_prompt(ev(text), text) == ""
    assert time.monotonic() - t0 < 1.5
    lst.close()


def test_a_skipped_message_does_not_use_up_the_first_message(server, tmp_path):
    put_key()
    marker = str(tmp_path / "seen")
    jev.for_prompt(ev("yes"), "yes", marker)
    assert not os.path.exists(marker)
    text = "fix the failing export button test in the billing module please"
    jev.for_prompt(ev(text), text, marker)
    jev.for_prompt(ev(text), text, marker)
    assert "reply" not in server[0]["body"]["questions"] and "reply" in server[1]["body"]["questions"]


def test_a_refused_key_stops_calls_until_it_is_fetched_again(monkeypatch):
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(1)
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NODARIS_JEV_URL", "http://127.0.0.1:%d/x" % srv.server_port)
    put_key()
    text = "fix the failing export button test in the billing module please"
    jev.for_prompt(ev(text), text)
    jev.for_prompt(ev(text), text)
    srv.shutdown()
    assert len(seen) == 1 and jev.status()["key_refused"]
    ok, _ = jev.fetch_key(runner=runner_ok(KEY))
    assert ok and not jev.status()["key_refused"]


def test_breaker_counts_failures_in_a_row_only():
    put_key()
    now = time.time()
    for rec in ({"ok": False}, {"ok": True}, {"ok": False}):
        jev._log(dict(rec, t=now))
    assert jev._recent_failures() == 1


def test_fetch_key_requires_the_named_field():
    ok, _ = jev.fetch_key(runner=runner_ok(json.dumps({"AWS_SECRET": KEY})))
    assert not ok and not os.path.exists(jev.key_path())


def test_fetch_key_replaces_a_planted_tmp_symlink(tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("")
    os.makedirs(os.path.dirname(jev.key_path()), exist_ok=True)
    os.symlink(str(target), jev.key_path() + ".tmp")
    ok, _ = jev.fetch_key(runner=runner_ok(KEY))
    assert ok and target.read_text() == "" and open(jev.key_path()).read().strip() == KEY


def test_fetch_key_reports_a_write_failure(monkeypatch):
    real_open = os.open

    def boom(path, *a, **kw):
        if str(path).endswith(".tmp"):
            raise PermissionError("denied")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(os, "open", boom)
    ok, msg = jev.fetch_key(runner=runner_ok(KEY))
    assert not ok and "could not be saved" in msg and KEY not in msg
