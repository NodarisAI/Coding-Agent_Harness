"""Start Claude Code the Nodaris way: the splash, then Claude Code with the live token panel beside it.

`nodaris` with no command, `nodaris start`, or `nodaris <claude arguments>` all come here. Claude Code itself runs
unchanged (the harness hooks are already in its settings); this only arranges the screen:

  - outside tmux, with tmux installed: a tmux session with Claude Code on the left and the panel on the right; the
    session closes when Claude Code exits;
  - inside tmux: the panel opens in a pane on the right and Claude Code runs in this pane;
  - without tmux: Claude Code runs here and the panel is offered in a second window (a new Terminal or iTerm window on
    macOS);
  - print mode (-p), --no-panel, or output that is not a terminal: Claude Code alone.
"""
import os, shlex, shutil, subprocess, sys

from . import hosts

PANEL_WIDTH = "38%"
PRINT_FLAGS = ("-p", "--print")
INSTALL_HINT = ("Claude Code is not installed. Install it with `curl -fsSL https://claude.ai/install.sh | bash`, "
                "then run nodaris again.")


class LaunchError(Exception):
    pass


def panel_command():
    return " ".join(shlex.quote(x) for x in (sys.executable, hosts.bin_path(), "watch", "--host", "claude"))


def plan(args, env=None, which=shutil.which, cwd=None, tty=True, pid=None):
    """The steps that start Claude Code: a list of (kind, argv) where kind is run, exec or note."""
    env = os.environ if env is None else env
    claude = which("claude")
    if not claude:
        raise LaunchError(INSTALL_HINT)
    no_panel = "--no-panel" in args
    args = [a for a in args if a != "--no-panel"]
    run_claude = [claude] + args
    if no_panel or not tty or any(a in PRINT_FLAGS for a in args):
        return [("exec", run_claude)]
    panel = panel_command()
    if env.get("TMUX") and which("tmux"):
        return [("run", ["tmux", "split-window", "-h", "-l", PANEL_WIDTH, "-d", panel]), ("exec", run_claude)]
    if which("tmux"):
        name = "nodaris-%d" % (pid or os.getpid())
        inner = " ".join(shlex.quote(x) for x in run_claude) + " ; tmux kill-session -t " + name
        return [("exec", ["tmux", "new-session", "-s", name, "-c", cwd or os.getcwd(), inner,
                          ";", "set-option", "-t", name, "mouse", "on",
                          ";", "set-option", "-t", name, "status", "off",
                          ";", "split-window", "-t", name, "-h", "-l", PANEL_WIDTH, "-d", panel])]
    return [("note", ["The token panel opens beside Claude Code when tmux is installed (brew install tmux). "
                      "Until then, run this in a second window: " + panel]),
            ("exec", run_claude)]


def start(args, out=None):
    from . import monitor, tui
    out = out or sys.stdout
    try:
        tty = sys.stdin.isatty() and out.isatty()
    except (AttributeError, ValueError):
        tty = False
    try:
        steps = plan(list(args), tty=tty)
    except LaunchError as e:
        out.write(str(e) + "\n")
        return 1
    if tty and not any(a in PRINT_FLAGS for a in args):
        tui.splash(out, duration=0.9, tagline="Claude Code with the Nodaris harness")
    for kind, argv in steps:
        if kind == "note":
            opened = sys.platform == "darwin" and os.environ.get("TERM_PROGRAM") in ("Apple_Terminal", "iTerm.app")
            if opened:
                monitor.split(type("A", (), {"open": True, "session": None})(), out)
            else:
                out.write(argv[0] + "\n")
        elif kind == "run":
            subprocess.run(argv, check=False)
        else:
            out.flush()
            os.execvp(argv[0], argv)
    return 0
