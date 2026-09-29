import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import copylint, dispatch  # noqa: E402

SLOP = '''export function Hero() {
  return (<section>
    <h1>Seamlessly supercharge your revenue cycle</h1>
    <p>Oops! Something went wrong — please try again</p>
    <Button label="Press the button to submit" />
  </section>);
}
'''
CLEAN = '''export function Claims({ rows }) {
  // leverage the cached rows here
  return (<section className="grid gap-4">
    <h1>Claims ready for review</h1>
    <p>The file could not be read. Check that it is an 835 file and upload it again.</p>
    <Button label="Click Approve to send the selected claims" />
  </section>);
}
'''


def test_slop_in_visible_text_is_named_with_the_line():
    found = copylint.lint(SLOP)
    rules = {f["rule"] for f in found}
    assert {"inflated", "cheer", "chat"} <= rules
    assert all(f["line"] > 0 for f in found)


def test_plain_copy_and_code_comments_pass():
    assert copylint.lint(CLEAN) == []


def test_only_ui_files_are_checked():
    assert copylint.advice("app/views.py", SLOP) == ""
    assert copylint.is_ui_file("src/locales/en.json") and copylint.is_ui_file("web/Hero.tsx")
    assert "plain-copy" in copylint.advice("web/Hero.tsx", SLOP)


def test_the_post_edit_hook_carries_the_advice(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    ev = {"hook_event_name": "PostToolUse", "session_id": "c1", "cwd": str(tmp_path), "tool_name": "Write",
          "tool_input": {"file_path": str(tmp_path / "Hero.tsx"), "content": SLOP}}
    out = dispatch.post_tool(ev)
    assert out["decision"] == "allow" and "Copy check on Hero.tsx" in out["context"]
