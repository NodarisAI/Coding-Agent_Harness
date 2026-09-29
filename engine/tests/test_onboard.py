"""Onboarding: settings validation, pack mapping, budgets, the registry, discovery, the app questions and the installer."""
import base64, io, json, os, subprocess, sys, types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "engine"))
from nodaris_harness import cli, hosts, onboard  # noqa: E402

BASE = {"company": "Example Health", "use": ["general"], "role": "engineer", "hosts": ["claude"],
        "reply_style": "brief", "plan": "pro", "team_sync": False}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("NODARIS_HARNESS_NO_BG", "1")
    (tmp_path / "user").mkdir()
    return tmp_path


def no_tools(name):
    return None


def fake_git(args, **kw):
    return types.SimpleNamespace(returncode=0, stdout="Jane.Doe@nodaris.ai\n", stderr="")


# ---- settings and validation ------------------------------------------------------------------------------------

def test_build_fills_defaults_and_validates():
    s = onboard.build(BASE, which=no_tools)
    assert s["version"] == 1 and s["packs"] == ["core"] and s["subagent_budget_tokens"] == 150000
    assert s["team_sync"] == {"enabled": False, "repo": "", "handle": ""}
    assert onboard.validate(s)


@pytest.mark.parametrize("uses,packs", [(["healthcare"], ["core", "healthcare"]), (["creative"], ["core", "creative"]),
                                        (["general"], ["core"]),
                                        (["healthcare", "creative", "general"], ["core", "healthcare", "creative"])])
def test_pack_mapping_unions_the_uses(uses, packs):
    assert onboard.packs_for(uses) == packs
    assert onboard.build(dict(BASE, use=uses), which=no_tools)["packs"] == packs


@pytest.mark.parametrize("plan,budget", [("pro", 150000), ("max", 600000), ("team", 400000), ("api", 300000)])
def test_budget_defaults_by_plan(plan, budget):
    assert onboard.default_budget(plan) == budget
    assert onboard.build(dict(BASE, plan=plan), which=no_tools)["subagent_budget_tokens"] == budget


def test_option_labels_are_accepted_as_answers():
    s = onboard.build({"company": "Acme", "use": ["Websites and motion"], "role": "Practice staff",
                       "hosts": ["Claude Code", "Gemini CLI"], "reply_style": "Teach", "plan": "Team"}, which=no_tools)
    assert (s["use"], s["role"], s["hosts"], s["reply_style"], s["plan"]) == \
        (["creative"], "practice-staff", ["claude", "gemini"], "teach", "team")


@pytest.mark.parametrize("change,field", [({"company": ""}, "company"), ({"role": "manager"}, "role"),
                                          ({"use": ["gaming"]}, "use"), ({"hosts": ["vim"]}, "hosts"),
                                          ({"plan": "free"}, "plan"), ({"reply_style": "loud"}, "reply_style"),
                                          ({"subagent_budget_tokens": 5}, "subagent_budget_tokens"),
                                          ({"subagent_budget_tokens": "lots"}, "subagent_budget_tokens"),
                                          ({"packs": ["pirate"]}, "packs"),
                                          ({"team_sync": {"enabled": True, "repo": "not a repo", "handle": "x"}},
                                           "team_sync.repo")])
def test_validation_errors_name_the_field(change, field):
    with pytest.raises(onboard.SettingsError) as e:
        onboard.build(dict(BASE, **change), which=no_tools)
    assert e.value.field == field and field in str(e.value)


def test_packs_always_include_core():
    assert onboard.build(dict(BASE, packs=["creative"]), which=no_tools)["packs"] == ["core", "creative"]
    s = onboard.build(BASE, which=no_tools)
    s["packs"] = ["creative"]
    with pytest.raises(onboard.SettingsError) as e:
        onboard.validate(s)
    assert e.value.field == "packs"


def test_nodaris_defaults_healthcare_and_suggests_team_sync(monkeypatch):
    s = onboard.build({"company": "NodarisAI", "team_sync": True}, which=no_tools, runner=fake_git)
    assert s["is_nodaris"] and s["use"] == ["healthcare"] and "healthcare" in s["packs"]
    assert s["team_sync"] == {"enabled": True, "repo": "NodarisAI/harness-team-data", "handle": "jane.doe"}


def test_save_load_show_and_reset(capsys):
    assert not onboard.is_onboarded()
    onboard.save(onboard.build(BASE, which=no_tools))
    assert onboard.is_onboarded()
    assert onboard.run_cli(["--show"]) == 0 and '"company": "Example Health"' in capsys.readouterr().out
    assert onboard.run_cli(["--reset"]) == 0 and not onboard.is_onboarded()


def test_answers_flag_saves_and_reports_field_errors(capsys):
    assert cli.main(["onboard", "--answers", json.dumps(BASE)]) == 0
    assert onboard.load()["company"] == "Example Health"
    assert cli.main(["onboard", "--answers", json.dumps(dict(BASE, role="wizard"))]) == 2
    assert "role" in capsys.readouterr().err
    assert cli.main(["onboard", "--answers", "{not json"]) == 2


def test_selected_packs_follow_the_saved_settings():
    onboard.save(onboard.build(dict(BASE, use=["creative"]), which=no_tools))
    assert hosts.selected_packs() == ["core", "creative"]


# ---- registry and discovery -------------------------------------------------------------------------------------

def test_registry_lookup_by_alias_and_domain():
    for name in ("Nodaris", "nodaris ai", "NodarisAI", "nodaris.ai", "Nodaris, Inc."):
        assert onboard.registry_match(name)["id"] == "nodaris", name
    assert onboard.registry_match("anthropic")["marketplace"]["repo"] == "anthropics/claude-plugins-official"
    assert onboard.registry_match("Nobody Corp") is None


def test_registry_entries_are_complete():
    reg = onboard.load_registry()
    for e in reg["entries"]:
        assert e["names"] and e["aliases"] and e["description"] and e["marketplace"]["add"]
        assert isinstance(e["verified"], bool)
        for p in e["plugins"]:
            assert p["install"][-1].endswith("--scope user"), p["name"]
    nod = onboard.registry_match("nodaris")
    assert nod["plugins"][0]["install"] == ["claude plugin marketplace add NodarisAI/Nodaris-Memory-Vault",
                                            "claude plugin install nodaris-context@nodaris --scope user"]


def test_discover_without_claude_or_gh_is_registry_only(monkeypatch):
    calls = []
    runner = lambda *a, **k: calls.append(a) or pytest.fail("no command may run")
    monkeypatch.setattr(onboard.shutil, "which", no_tools)
    found = onboard.discover("Nodaris", search=True, runner=runner)
    assert [(s["name"], s["origin"], s["verified"]) for s in found] == [("nodaris-context", "registry", True)]
    assert onboard.discover("Nobody Corp", search=True, runner=runner) == []
    assert calls == []


def test_discover_uses_known_marketplaces_and_skips_the_add_step(home, monkeypatch):
    plug = home / "user" / ".claude" / "plugins"
    loc = plug / "marketplaces" / "nodaris"
    (loc / ".claude-plugin").mkdir(parents=True)
    (loc / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": "nodaris", "plugins": [{"name": "nodaris-context"}]}))
    (plug / "known_marketplaces.json").write_text(json.dumps(
        {"nodaris": {"source": {"source": "git", "url": "https://github.com/NodarisAI/Nodaris-Memory-Vault.git"},
                     "installLocation": str(loc)}}))
    found = onboard.discover("Nodaris", which=lambda n: "/bin/" + n if n == "claude" else None)
    assert len(found) == 1 and found[0]["install"] == ["claude plugin install nodaris-context@nodaris --scope user"]


def test_search_results_are_labelled_unverified(monkeypatch):
    body = base64.b64encode(json.dumps({"name": "acme", "plugins": [{"name": "acme-kit"}]}).encode()).decode()

    def runner(args, **kw):
        if args[:3] == ["gh", "search", "repos"]:
            return types.SimpleNamespace(returncode=0, stdout=json.dumps([{"fullName": "acme/claude-plugins",
                                                                           "description": "", "stargazersCount": 3}]))
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"content": body}))
    found = onboard.discover("Acme", search=True, which=lambda n: "/bin/gh" if n == "gh" else None, runner=runner)
    assert found[0]["label"] == "found by search, not verified" and not found[0]["verified"]
    assert found[0]["install"] == ["claude plugin marketplace add acme/claude-plugins",
                                   "claude plugin install acme-kit@acme --scope user"]
    assert onboard.discover("Acme", search=False, which=lambda n: "/bin/gh", runner=runner) == []


def test_install_plugin_runs_each_command_and_stops_on_failure():
    ran = []

    def runner(args, **kw):
        ran.append(args)
        return types.SimpleNamespace(returncode=1 if len(ran) == 1 else 0)
    out = io.StringIO()
    ok = onboard.install_plugin({"install": ["claude plugin marketplace add a/b", "claude plugin install x@b --scope user"]},
                                runner, out)
    assert not ok and len(ran) == 1 and "Exit code 1." in out.getvalue()


# ---- Claude Code app flow ---------------------------------------------------------------------------------------

def test_app_questions_fit_the_ask_user_question_limits():
    rounds = onboard.app_questions()
    assert len(rounds) == 2
    for r in rounds:
        assert 1 <= len(r) <= 4
        for q in r:
            assert len(q["header"]) <= 12 and "?" in q["question"]
            assert 2 <= len(q["options"]) <= 4
            assert isinstance(q["multiSelect"], bool)
            assert all(o["label"] and o["description"] for o in q["options"])


def test_app_answers_round_trip_through_build():
    labels = {q["header"]: q["options"][0]["label"] for r in onboard.app_questions() for q in r}
    s = onboard.build({"company": "Acme", "use": [labels["Use"]], "role": labels["Role"],
                       "hosts": ["Claude Code", labels["Agents"]], "is_nodaris": labels["Team"],
                       "reply_style": labels["Replies"], "plan": labels["Plan"], "team_sync": "No"}, which=no_tools,
                      runner=fake_git)
    assert s["hosts"] == ["claude", "codex"] and s["is_nodaris"] is True


def test_app_instructions_are_compact_and_complete():
    text = onboard.app_instructions("/opt/h/bin/nodaris-harness")
    prose = text
    for r in onboard.app_questions():
        prose = prose.replace(json.dumps(r, separators=(",", ":")), "")
    assert len(prose.split()) < 350
    assert "AskUserQuestion" in text and "onboard --answers" in text and 'onboard --discover "<company>"' in text
    assert "says yes" in text


# ---- installer --------------------------------------------------------------------------------------------------

def snapshot(d):
    return sorted(os.path.join(r, f) for r, ds, fs in os.walk(d) for f in fs + ds)


def test_install_dry_run_yes_answers_writes_nothing(tmp_path):
    user = tmp_path / "fresh"
    user.mkdir()
    env = dict(os.environ, HOME=str(user), NODARIS_HARNESS_HOME=str(user / ".nodaris-harness"), NODARIS_HARNESS_NO_BG="1")
    before = snapshot(user)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), "--dry-run", "--yes", "--answers",
                        json.dumps(dict(BASE, hosts=["claude", "codex", "gemini", "opencode", "cursor"]))],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    assert "Dry run: nothing was written for claude." in p.stdout and "\x1b[" not in p.stdout
    assert snapshot(user) == before == []


def test_install_yes_without_answers_is_refused_with_the_field(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), NODARIS_HARNESS_HOME=str(tmp_path / "h"))
    p = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), "--yes"], capture_output=True, text=True,
                       env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert p.returncode == 2 and "answers" in p.stderr


def test_install_then_uninstall_round_trip(tmp_path):
    user = tmp_path / "u"
    (user / ".codex").mkdir(parents=True)
    env = dict(os.environ, HOME=str(user), NODARIS_HARNESS_HOME=str(user / ".nodaris-harness"), NODARIS_HARNESS_NO_BG="1")
    run = lambda *a: subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), *a], capture_output=True,
                                    text=True, env=env, timeout=600, stdin=subprocess.DEVNULL)
    p = run("--yes", "--host", "codex", "--answers", json.dumps(dict(BASE, hosts=["codex"])))
    assert p.returncode == 0, p.stdout + p.stderr
    assert (user / ".codex" / "hooks.json").exists() and "Installed and checked for codex." in p.stdout
    p = run("--uninstall", "--yes")
    assert p.returncode == 0, p.stderr
    assert not (user / ".codex" / "hooks.json").exists() and not (user / ".agents" / "skills").exists() or \
        not os.listdir(user / ".agents" / "skills")


def test_only_plain_plugin_commands_are_ever_run():
    from nodaris_harness import onboard
    ok = ["claude plugin marketplace add NodarisAI/Nodaris-Memory-Vault", "claude plugin install nodaris-context@nodaris --scope user"]
    bad = ["claude plugin install x@y --scope user; rm -rf ~", "claude plugin install --config=evil x --scope user",
           "claude plugin marketplace add https://evil.example/x.git --foo", "bash -c 'claude plugin install x'"]
    assert all(onboard._allowed_install(c) for c in ok)
    assert not any(onboard._allowed_install(c) for c in bad)
    ran = []
    assert onboard.install_plugin({"install": bad[:1]}, runner=lambda *a, **k: ran.append(a)) is False and ran == []
