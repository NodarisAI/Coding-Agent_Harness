import os, shlex, sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nodaris_harness import cli, hosts, launch  # noqa: E402


def which_all(name):
    return "/usr/local/bin/" + name


def which_no_tmux(name):
    return None if name == "tmux" else "/usr/local/bin/" + name


def steps_of(plan, kind):
    return [argv for k, argv in plan if k == kind]


def test_outside_tmux_opens_a_session_with_claude_and_the_panel():
    plan = launch.plan(["--continue"], env={}, which=which_all, cwd="/work/app", tty=True, pid=42)
    (argv,) = steps_of(plan, "exec")
    assert argv[:6] == ["tmux", "new-session", "-s", "nodaris-42", "-c", "/work/app"]
    joined = " ".join(argv)
    assert "claude --continue" in argv[6] and "tmux kill-session -t nodaris-42" in argv[6]
    assert "split-window" in argv and "watch --host claude" in joined and "38%" in argv


def test_inside_tmux_splits_this_window_then_runs_claude_here():
    plan = launch.plan([], env={"TMUX": "/tmp/tmux-1/default,1,0"}, which=which_all, cwd="/w", tty=True, pid=1)
    (split,) = steps_of(plan, "run")
    assert split[:5] == ["tmux", "split-window", "-h", "-l", "38%"] and "watch --host claude" in split[-1]
    assert steps_of(plan, "exec") == [["/usr/local/bin/claude"]]


def test_without_tmux_claude_still_starts_and_the_panel_is_explained():
    plan = launch.plan([], env={}, which=which_no_tmux, cwd="/w", tty=True, pid=1)
    assert steps_of(plan, "exec") == [["/usr/local/bin/claude"]]
    assert any("watch" in " ".join(a) for a in steps_of(plan, "note"))


@pytest.mark.parametrize("args", [["-p", "summarise"], ["--print", "x"], ["--no-panel"]])
def test_print_mode_and_no_panel_run_claude_alone(args):
    plan = launch.plan(args, env={}, which=which_all, cwd="/w", tty=True, pid=1)
    (argv,) = steps_of(plan, "exec")
    assert argv[0] == "/usr/local/bin/claude" and "--no-panel" not in argv and not steps_of(plan, "run")


def test_not_a_terminal_runs_claude_alone():
    plan = launch.plan([], env={}, which=which_all, cwd="/w", tty=False, pid=1)
    assert steps_of(plan, "exec") == [["/usr/local/bin/claude"]]


def test_missing_claude_says_how_to_install_it():
    with pytest.raises(launch.LaunchError) as e:
        launch.plan([], env={}, which=lambda n: None, cwd="/w", tty=True, pid=1)
    assert "claude.ai/install.sh" in str(e.value)


def test_arguments_with_spaces_survive_the_tmux_command_line():
    plan = launch.plan(["--append-system-prompt", "be brief; really"], env={}, which=which_all, cwd="/w", tty=True, pid=7)
    (argv,) = steps_of(plan, "exec")
    inner = argv[6]
    assert shlex.split(inner)[2:5] == ["/usr/local/bin/claude", "--append-system-prompt", "be brief; really"]


def test_the_panel_follows_the_session_this_command_starts():
    for env in ({}, {"TMUX": "/tmp/tmux-1/default,1,0"}):
        plan = launch.plan(["--resume"], env=env, which=which_all, cwd="/w", tty=True, pid=3, link="0123456789abcdef")
        joined = " ".join(" ".join(a) for _, a in plan)
        assert "watch --host claude --link 0123456789abcdef" in joined
        assert ("setenv", ["NODARIS_PANEL_LINK", "0123456789abcdef"]) in plan or "NODARIS_PANEL_LINK=0123456789abcdef" in joined
    (argv,) = steps_of(launch.plan([], env={}, which=which_all, cwd="/w", tty=True, pid=3, link="00ff00ff00ff00ff"), "exec")
    assert shlex.split(argv[6])[:2] == ["env", "NODARIS_PANEL_LINK=00ff00ff00ff00ff"]
    tm = {"TMUX": "x"}
    assert launch.plan([], env=tm, which=which_all, tty=True)[0][1][1] != launch.plan([], env=tm, which=which_all, tty=True)[0][1][1]


def test_no_arguments_starts_claude_instead_of_a_usage_error(monkeypatch):
    seen = []
    monkeypatch.setattr(launch, "start", lambda args: seen.append(args) or 0)
    assert cli.main([]) == 0 and seen == [[]]
    assert cli.main(["--continue"]) == 0 and seen[-1] == ["--continue"]
    assert cli.main(["start", "--resume"]) == 0 and seen[-1] == ["--resume"]


def test_help_is_still_the_harness_help(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "nodaris-harness" in capsys.readouterr().out


def test_commands_are_linked_onto_the_path_and_removed_again(tmp_path):
    home = tmp_path
    made = hosts.link_commands(home=str(home))
    local = home / ".local" / "bin"
    for name in ("nodaris", "nodaris-harness"):
        assert os.path.realpath(local / name) == os.path.realpath(hosts.bin_path())
    assert sorted(made) == sorted(str(local / n) for n in ("nodaris", "nodaris-harness"))
    assert hosts.link_commands(home=str(home)) == []          # already there: nothing new
    hosts.unlink_commands(home=str(home))
    assert not (local / "nodaris").exists() and not (local / "nodaris-harness").exists()


def test_a_command_the_person_already_has_is_never_replaced(tmp_path):
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "nodaris").write_text("#!/bin/sh\necho mine\n")
    made = hosts.link_commands(home=str(tmp_path))
    assert (local / "nodaris").read_text() == "#!/bin/sh\necho mine\n"
    assert str(local / "nodaris") not in made
    hosts.unlink_commands(home=str(tmp_path))
    assert (local / "nodaris").read_text() == "#!/bin/sh\necho mine\n"


def test_a_dangling_link_from_a_moved_harness_is_repaired(tmp_path):
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    os.symlink(str(tmp_path / "gone" / "bin" / "nodaris-harness"), str(local / "nodaris"))
    made = hosts.link_commands(home=str(tmp_path))
    assert str(local / "nodaris") in made
    assert os.path.realpath(local / "nodaris") == os.path.realpath(hosts.bin_path())
