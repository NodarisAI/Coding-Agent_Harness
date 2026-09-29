import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import designlint  # noqa: E402

SLOP = '''<h1 className="bg-gradient-to-r from-purple-500 to-blue-500 bg-clip-text text-transparent">Claims</h1>
<div className="rounded-xl border-l-4 border-teal-500 p-4">Denials</div>
<button className="bg-indigo-600 text-gray-400">Approve</button>
.card { transition: transform 300ms cubic-bezier(0.34, 1.56, 0.64, 1); }
<aside className="backdrop-blur-md bg-white/10">Totals</aside>
'''
CLEAN = '''<h1 className="text-2xl font-semibold text-slate-900">Claims ready for review</h1>
<table className="w-full text-sm"><tr className="border-b"><td>12</td></tr></table>
<button className="bg-teal-700 text-white">Approve 12 claims</button>
.row { transition: background-color 120ms ease-out; }
'''


def test_each_rule_fires_on_its_habit():
    rules = {f["rule"] for f in designlint.lint(SLOP)}
    assert {"gradient-text", "side-stripe", "gray-on-colour", "bounce", "glass"} <= rules


def test_the_purple_to_blue_palette_is_named():
    assert designlint.lint('<div className="bg-gradient-to-r from-violet-600 to-cyan-400">')[0]["rule"] == "ai-palette"


def test_plain_business_ui_passes():
    assert designlint.lint(CLEAN) == []


def test_an_ignore_needs_a_reason():
    line = '<div className="rounded border-l-4">{/* design-lint-ignore: */}</div>'
    assert designlint.lint(line)
    assert designlint.lint(line.replace("ignore: ", "ignore: brand timeline marker ")) == []


def test_only_style_files_are_checked():
    assert designlint.advice("api/views.py", SLOP) == "" and "Design check" in designlint.advice("web/App.tsx", SLOP)
