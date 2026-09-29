"""Jev: privacy before anything is sent, the team key from AWS, budgets and switches, and the setup checklist."""
import http.server, json, os, stat, sys, threading, types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "engine"))
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


def put_key():
    os.makedirs(os.path.dirname(jev.key_path()), exist_ok=True)
    with open(jev.key_path(), "w") as fh:
        fh.write(KEY + "\n")


def ev(prompt, session="s1"):
    return {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": session, "cwd": os.getcwd()}


def test_no_key_adds_nothing_and_sends_nothing(server):
    assert jev.for_prompt(ev("fix the failing export button test in the billing module"), "fix the failing export button test in the billing module", True) == ""
    assert server == []


def test_ordinary_message_is_read_and_shaped(server):
    put_key()
    text = "fix the failing export button test in the billing module please"
    out = jev.for_prompt(ev(text), text, True)
    assert "Route: fix · effort medium" in out and "Reply shape: brief" in out
    assert server[0]["auth"] == "Bearer " + KEY
    assert server[0]["body"]["model"] == jev.MODEL and "reply" not in server[0]["body"]["questions"]
    assert KEY not in json.dumps(server[0]["body"])


def test_patient_identifiers_are_never_sent(server):
    put_key()
    text = "Patient John Smith, SSN 123-45-6789, DOB 03/14/1961, member ID ABC123456789 was denied, fix the claim"
    assert jev.for_prompt(ev(text), text, True) == ""
    assert server == []


def test_credentials_are_masked_before_sending(server):
    put_key()
    text = "the deploy fails with key sk-live-" + "Zz9" * 10 + " and AKIAABCDEFGHIJKLMNOP, why does it fail"
    jev.for_prompt(ev(text), text, True)
    sent = server[0]["body"]["state"]["message"]
    assert "sk-live-" not in sent and "AKIA" not in sent and "[SECRET-" in sent


def test_short_slash_subagent_and_off_are_skipped(server, monkeypatch):
    put_key()
    assert jev.for_prompt(ev("yes"), "yes", False) == ""
    assert jev.for_prompt(ev("/review the whole branch now please ok"), "/review the whole branch now please ok", False) == ""
    sub = dict(ev("x"), agent_id="a1")
    assert jev.for_prompt(sub, "explain how the router picks a playbook for a request", False) == ""
    jev.set_enabled(False)
    assert jev.for_prompt(ev("x"), "explain how the router picks a playbook for a request", False) == ""
    jev.set_enabled(True)
    monkeypatch.setenv("NODARIS_JEV", "off")
    assert jev.for_prompt(ev("x"), "explain how the router picks a playbook for a request", False) == ""
    assert server == []


def test_daily_cap_stops_calls(server, monkeypatch):
    put_key()
    monkeypatch.setattr(jev, "DAILY_CAP_USD", 0.0001)
    text = "fix the failing export button test in the billing module please"
    jev.for_prompt(ev(text), text, True)
    jev.for_prompt(ev(text), text, True)
    assert len(server) == 1


def test_network_failures_open_a_breaker(monkeypatch):
    put_key()
    monkeypatch.setenv("NODARIS_JEV_URL", "http://127.0.0.1:9/api/alpha/decisions")
    text = "fix the failing export button test in the billing module please"
    for _ in range(3):
        assert jev.for_prompt(ev(text), text, True) == ""
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
