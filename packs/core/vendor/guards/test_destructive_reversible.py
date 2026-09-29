"""Reversible-delete mode in the destructive guard."""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "destructive" + "-guard.py")


def run(command, **env):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": os.path.expanduser("~")}
    home = tempfile.mkdtemp()
    e = dict(os.environ, NODARIS_HARNESS_HOME=home, **env)
    e.pop("NODARIS_REVERSIBLE_DELETE", None) if "NODARIS_REVERSIBLE_DELETE" not in env else None
    return subprocess.run([sys.executable, GUARD], input=json.dumps(payload), capture_output=True, text=True, env=e)


class Reversible(unittest.TestCase):
    TARGET = "rm -rf ~/Documents/guard-test-not-a-real-folder"

    def test_off_by_default_keeps_the_logged_behaviour(self):
        self.assertEqual(run(self.TARGET).returncode, 0)

    def test_on_refuses_with_the_trash_command(self):
        r = run(self.TARGET, NODARIS_REVERSIBLE_DELETE="1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("trash", json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_the_settings_file_turns_it_on(self):
        home = tempfile.mkdtemp()
        with open(os.path.join(home, "settings.json"), "w") as fh:
            json.dump({"reversible_delete": True}, fh)
        payload = {"tool_name": "Bash", "tool_input": {"command": self.TARGET}, "cwd": os.path.expanduser("~")}
        e = {k: v for k, v in os.environ.items() if k != "NODARIS_REVERSIBLE_DELETE"}
        e["NODARIS_HARNESS_HOME"] = home
        r = subprocess.run([sys.executable, GUARD], input=json.dumps(payload), capture_output=True, text=True, env=e)
        self.assertEqual(r.returncode, 2)

    def test_an_explicit_override_and_a_single_file_still_pass(self):
        self.assertEqual(run(self.TARGET + "  # guard:ok", NODARIS_REVERSIBLE_DELETE="1").returncode, 0)
        self.assertEqual(run("rm notes.txt", NODARIS_REVERSIBLE_DELETE="1").returncode, 0)

    def test_other_recursive_delete_forms_go_to_the_trash_too(self):
        self.assertEqual(run("cat > t.py <<'EOF'\\nshutil.rmtree(x)\\nEOF", NODARIS_REVERSIBLE_DELETE="1").returncode, 0)
        self.assertEqual(run("find ./old-output -type d -empty -delete", NODARIS_REVERSIBLE_DELETE="1").returncode, 0)
        for cmd in ("rm -rf src", "python3 -c 'import shutil; shutil.rmtree(chr(115))'",
                    "node -e 'require(`fs`).rmSync(`src`, {recursive: true})'", "find . -name x -delete",
                    "git clean -fdx", "ls | xargs rm -rf"):
            self.assertEqual(run(cmd, NODARIS_REVERSIBLE_DELETE="1").returncode, 2, cmd)

    def test_build_folders_and_the_message_carry_no_override(self):
        self.assertEqual(run("rm -rf node_modules dist", NODARIS_REVERSIBLE_DELETE="1").returncode, 0)
        r = run(self.TARGET, NODARIS_REVERSIBLE_DELETE="1")
        self.assertNotIn("guard:ok", r.stdout)

    def test_a_home_wipe_is_still_a_hard_deny(self):
        self.assertEqual(run("rm -rf ~", NODARIS_REVERSIBLE_DELETE="1").returncode, 2)


if __name__ == "__main__":
    unittest.main()
