"""Literal-credential detection in the secret guard. Fake values are assembled at run time, so this file holds none."""
import importlib.util, json, os, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "secret" + "-guard.py")
spec = importlib.util.spec_from_file_location("sg_literals", GUARD)
sg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sg)

ALNUM = "Q7m2Kp9Xr4Tz8Wv1Ls6Nd3Hb5Jf0Gc2Ya7Ue4"


def fake(prefix, n):
    return prefix + (ALNUM * 4)[:n]


class Literals(unittest.TestCase):
    def deny(self, tool, ti):
        return sg.decide(tool, ti, HERE)

    def test_written_tokens_are_refused(self):
        for value in (fake("gh" + "p_", 36), fake("gl" + "pat-", 24), fake("s" + "k_live_", 28), fake("AI" + "za", 35),
                      "AK" + "IA" + "QZ7M2KP9XR4TZ8WV", fake("sk-or-" + "v1-", 0) + "ab12" * 16):
            self.assertEqual(self.deny("Write", {"file_path": "/x/app.py", "content": f"KEY = '{value}'\n"}),
                             (True, "secret-literal"), value[:6])
        self.assertEqual(self.deny("Bash", {"command": "curl -H 'Authorization: " + fake("gh" + "p_", 40) + "' x"}),
                         (True, "secret-literal"))
        pem = "-----BEGIN OPENSSH " + "PRIVATE KEY-----\n" + "\n".join([ALNUM * 2] * 6) + "\n"
        self.assertEqual(self.deny("Edit", {"file_path": "/x/k", "old_string": "a", "new_string": pem}), (True, "secret-literal"))

    def test_placeholders_publishable_keys_and_ordinary_code_pass(self):
        for text in ("token = 'gh" + "p_EXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLE12'", "pk_" + "live_" + ALNUM,
                     "sk_" + "live_xxxxxxxxxxxxxxxxxxxxxxxx", "def total(a, b):\n    return a + b\n",
                     "pem = \"-----BEGIN RSA " + "PRIVATE KEY-----\\nMIIEpAIBAAKCAQEA\\n-----END RSA PRIVATE KEY-----\""):
            self.assertEqual(self.deny("Write", {"file_path": "/x/a.py", "content": text}), (False, None), text[:12])

    def test_the_value_is_never_echoed(self):
        value = fake("gh" + "p_", 36)
        payload = {"tool_name": "Write", "tool_input": {"file_path": "/x/a.py", "content": value}, "cwd": HERE}
        r = subprocess.run([sys.executable, GUARD], input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn(value, r.stdout + r.stderr)
        self.assertIn("GitHub token", r.stdout)


if __name__ == "__main__":
    unittest.main()
