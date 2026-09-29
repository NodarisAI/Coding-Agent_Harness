import json, os, subprocess, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import security as S  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    r = tmp_path / "app"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / "app.py").write_text("def view(request):\n    return request.user.id\n")
    return str(r)


def test_no_scope_means_static_only(repo, monkeypatch):
    monkeypatch.setattr(S, "static_tier", lambda repo: [{"tool": "semgrep", "ran": True, "rc": 0, "findings": [], "note": ""}])
    rep = S.assess(repo, targets=["https://staging.example.com"])
    assert rep["live"] == [] and any("no scope file" in s for s in rep["skipped"])
    assert os.path.exists(rep["log"])
    text = open(rep["log"]).read()
    assert "no findings" in text and "Skipped" in text


def test_signed_scope_admits_only_its_non_production_hosts(repo):
    envs = [{"name": "dev", "hosts": ["http://localhost:8000"], "checks": ["passive"]},
            {"name": "prod", "hosts": ["https://app.example.com"], "checks": ["passive"], "production": True}]
    scope, path = S.write_scope(repo, envs, "owner", lambda s: "yes")
    assert scope and os.path.exists(path)
    loaded, why = S.load_scope(repo)
    assert loaded and why == "ok"
    assert S.check_target("http://localhost:8000/login", loaded)[0] is True
    ok, reason, _ = S.check_target("https://app.example.com", loaded)
    assert ok is False and "production" in reason
    ok, reason, _ = S.check_target("https://staging.example.com", loaded)
    assert ok is False and "not in the signed scope" in reason
    ok, reason, _ = S.check_target("https://www.somewhere.com", loaded)
    assert ok is False and "production host" in reason


def test_a_declined_or_edited_scope_does_not_count(repo):
    assert S.write_scope(repo, [{"name": "dev", "hosts": ["http://localhost:8000"]}], "owner", lambda s: "no") == (None, "not signed")
    S.write_scope(repo, [{"name": "dev", "hosts": ["http://localhost:8000"]}], "owner", lambda s: "yes")
    p = S.scope_path(repo)
    data = json.load(open(p))
    data["environments"][0]["hosts"].append("https://app.example.com")
    json.dump(data, open(p, "w"))
    loaded, why = S.load_scope(repo)
    assert loaded is None and "signature" in why


def test_live_tier_runs_only_for_in_scope_targets(repo, monkeypatch):
    S.write_scope(repo, [{"name": "dev", "hosts": ["http://localhost:8000"]}], "owner", lambda s: "yes")
    calls = []
    monkeypatch.setattr(S, "static_tier", lambda repo: [])
    monkeypatch.setattr(S, "live_tier", lambda url, env, repo: (calls.append(url) or {"tool": "zap-baseline", "ran": True, "rc": 0, "findings": [
        {"severity": "Medium", "finding": "Missing header for member id W123456789", "where": url}], "note": ""}))
    rep = S.assess(repo, targets=["http://localhost:8000", "https://staging.example.com"])
    assert calls == ["http://localhost:8000"]
    assert any("staging.example.com" in s for s in rep["skipped"])
    text = open(rep["log"]).read()
    assert "Medium" in text and "W123456789" not in text
    assert "Findings: Medium 1" in S.summary(rep)
