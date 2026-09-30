import os, subprocess, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import trash  # noqa: E402

CLI = os.path.join(os.path.dirname(__file__), "..", "..", "bin", "nodaris-harness")


def test_a_folder_moves_to_the_trash_and_comes_back_intact(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    (tmp_path / "build" / "sub").mkdir(parents=True)
    (tmp_path / "build" / "sub" / "a.txt").write_text("one")
    (tmp_path / "notes.md").write_text("two")
    entry, msg = trash.move(["build", "notes.md"], str(tmp_path))
    assert entry and not (tmp_path / "build").exists() and not (tmp_path / "notes.md").exists()
    assert trash.entries()[0]["items"][0]["bytes"] == 3
    ok, _ = trash.restore(entry)
    assert ok and (tmp_path / "build" / "sub" / "a.txt").read_text() == "one" and (tmp_path / "notes.md").exists()
    assert trash.entries() == []


def test_restore_never_overwrites_and_the_home_folder_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    (tmp_path / "f.txt").write_text("old")
    entry, _ = trash.move(["f.txt"], str(tmp_path))
    (tmp_path / "f.txt").write_text("new")
    ok, msg = trash.restore(entry)
    assert not ok and (tmp_path / "f.txt").read_text() == "new" and "exist again" in msg
    assert trash.move(["~"], str(tmp_path))[0] is None
    assert trash.move(["missing.txt"], str(tmp_path))[0] is None


def test_the_cli_moves_and_lists(tmp_path):
    env = dict(os.environ, NODARIS_HARNESS_HOME=str(tmp_path / "home"), NODARIS_HARNESS_NO_BG="1")
    (tmp_path / "x.log").write_text("x")
    r = subprocess.run([sys.executable, CLI, "trash", "x.log"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "Restore them with" in r.stdout and not (tmp_path / "x.log").exists()
    r = subprocess.run([sys.executable, CLI, "trash", "--list"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert "x.log" in r.stdout


def test_a_failed_move_puts_everything_back(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    real, calls = trash.shutil.move, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise PermissionError("no")
        return real(src, dst)
    monkeypatch.setattr(trash.shutil, "move", flaky)
    entry, msg = trash.move(["a.txt", "b.txt"], str(tmp_path))
    assert entry is None and "put back" in msg
    assert (tmp_path / "a.txt").read_text() == "a" and (tmp_path / "b.txt").exists() and trash.entries() == []


def test_the_harness_cannot_trash_itself(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(home))
    home.mkdir()
    (home / "settings.json").write_text("{}")
    assert trash.move([str(home / "settings.json")], str(tmp_path))[0] is None
    assert (home / "settings.json").exists()
