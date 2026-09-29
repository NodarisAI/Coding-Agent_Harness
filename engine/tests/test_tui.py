"""Terminal presentation: plain-text degradation, the banner width fallback, colour modes and the fallback menus."""
import io, os, re, sys, time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "engine"))
from nodaris_harness import tui  # noqa: E402

EMOJI = [(0x1F300, 0x1FAFF), (0x2600, 0x27BF)]


class FakeTTY(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("NO_COLOR", "NODARIS_REDUCED_MOTION", "COLORTERM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def run_everything(stream, monkeypatch, answers="\n\n\n\n\n"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(answers))
    start = time.time()
    tui.splash(stream)
    tui.type_out("Welcome to the harness.", stream)
    with tui.Spinner("Checking", stream):
        pass
    bar = tui.Progress("Copying", stream)
    bar.update(0.5)
    bar.done()
    tui.panel("Summary", "Company: Example\nA long sentence that must wrap inside the box. " * 3, stream)
    tui.choose("Pick one", [("First", "the first"), ("Second", "")], 1, stream)
    tui.choose_many("Pick some", ["A", "B", "C"], [0, 2], stream)
    tui.ask("Company?", "Acme", stream)
    tui.confirm("Continue?", True, stream)
    tui.line(tui.gradient("rainbow", 0, stream) + tui.accent("x", stream) + tui.bold("y", stream), stream)
    return time.time() - start


@pytest.mark.parametrize("setup", ["not-a-tty", "no-color", "dumb", "reduced"])
def test_plain_output_has_no_escape_codes_and_no_sleeping(setup, monkeypatch):
    stream = io.StringIO() if setup == "not-a-tty" else FakeTTY()
    if setup == "no-color":
        monkeypatch.setenv("NO_COLOR", "1")
    elif setup == "dumb":
        monkeypatch.setenv("TERM", "dumb")
    elif setup == "reduced":
        monkeypatch.setenv("NODARIS_REDUCED_MOTION", "1")
    assert tui.plain(stream) and tui.color_mode(stream) == "none"
    elapsed = run_everything(stream, monkeypatch)
    out = stream.getvalue()
    assert "\x1b" not in out
    assert elapsed < 0.5
    assert "Company?" in out and "Pick one" in out


def test_fallback_menus_return_defaults_and_numbers(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n2\n1, 3\n\nyes\nno\n"))
    assert tui.choose("One?", ["a", "b", "c"], 1, out) == 1
    assert tui.choose("One?", ["a", "b", "c"], 0, out) == 1
    assert tui.choose_many("Many?", ["a", "b", "c"], [], out) == [0, 2]
    assert tui.ask("Name?", "Acme", out) == "Acme"
    assert tui.confirm("Sure?", False, out) is True
    assert tui.confirm("Sure?", True, out) is False
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert tui.choose_many("Many?", ["a", "b"], [1], out) == [1]
    assert tui.confirm("Sure?", True, out) is True


def test_banner_falls_back_to_compact_under_sixty_columns():
    big = tui.banner_lines(100)
    assert len(big) == 5 and all(len(line) <= 58 for line in big)
    assert tui.banner_lines(59) == [tui.COMPACT]
    assert tui.banner_lines(40) == [tui.COMPACT]
    assert tui.banner_lines(100, unicode=False) == [tui.COMPACT]
    assert len(tui.banner_lines(60)) == 5


def test_colour_modes(monkeypatch):
    s = FakeTTY()
    assert tui.color_mode(s) == "256" and "\x1b[38;5;" in tui.paint("x", (10, 200, 180), s)
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert tui.color_mode(s) == "truecolor" and "\x1b[38;2;10;200;180m" in tui.paint("x", (10, 200, 180), s)
    g = tui.gradient("NODARIS", 0.0, s)
    assert tui.strip(g) == "NODARIS" and g.count("\x1b[38;2;") >= 5


def test_splash_animates_briefly_in_a_terminal(monkeypatch):
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    s = FakeTTY()
    start = time.time()
    tui.splash(s, duration=0.3)
    assert time.time() - start < 2.0
    out = s.getvalue()
    assert "\x1b[?25l" in out and "\x1b[?25h" in out
    assert not tui._STATE["cursor_hidden"]


def test_panel_never_exceeds_the_terminal_width(monkeypatch):
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback=(80, 24): os.terminal_size((40, 24)))
    s = io.StringIO()
    tui.panel("Title", "word " * 60, s)
    assert all(len(line) <= 40 for line in s.getvalue().splitlines())


def test_product_text_has_no_emoji():
    src = open(tui.__file__, encoding="utf-8").read()
    src += open(os.path.join(ROOT, "engine", "nodaris_harness", "onboard.py"), encoding="utf-8").read()
    src += open(os.path.join(ROOT, "install.py"), encoding="utf-8").read()
    assert not [c for c in src if any(lo <= ord(c) <= hi for lo, hi in EMOJI)]


def test_progress_bar_text():
    assert tui.progress_bar(0.5, 10) == "█████░░░░░  50%"
    assert tui.progress_bar(2, 4, unicode=False) == "#### 100%"


def test_gradient_stays_in_the_teal_family(monkeypatch):
    monkeypatch.setenv("COLORTERM", "truecolor")
    s = FakeTTY()
    for phase in (0.0, 0.3, 0.77):
        g = tui.gradient("NODARIS HARNESS WORDMARK", phase, s, shimmer=None)
        for r, gg, b in (tuple(map(int, m)) for m in re.findall(r"38;2;(\d+);(\d+);(\d+)m", g)):
            assert gg > r + 60 and b > r + 50, (r, gg, b)       # green and blue lead: teal, never red, orange or purple
            assert abs(gg - b) < 60, (r, gg, b)
