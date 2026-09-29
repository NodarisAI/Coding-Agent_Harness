"""Code graphs: make a repository understandable before the agent reads it file by file.

Two tools, both open source and installed only with the person's yes (asked once during onboarding, or by running
`nodaris-harness graph --install`):
- code-review-graph: a structural graph of the code (functions, calls, imports, flows), served to the agent over MCP,
  so "what calls this" and "what breaks if this changes" are answered from the graph instead of by reading files.
- graphify: a knowledge graph of a folder with a report and a browsable page; `graphify update` re-reads code only
  and never calls a model.

`nodaris-harness graph [PATH]` registers and builds both for a repository.
"""
import os, shlex, shutil, subprocess

TOOLS = [("code-review-graph", "code-review-graph"), ("graphify", "graphifyy")]   # (command, package)
MCP_NAME = "code-review-graph"


def _run(argv, runner=None, timeout=900, cwd=None):
    run = runner or subprocess.run
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def missing(which=shutil.which):
    return [(cmd, pkg) for cmd, pkg in TOOLS if not which(cmd)]


def install_commands(which=shutil.which):
    """The exact commands that install the missing tools, or None when neither uv nor pipx is available."""
    need = missing(which)
    if not need:
        return []
    if which("uv"):
        return [f"uv tool install {pkg}" for _, pkg in need]
    if which("pipx"):
        return [f"pipx install {pkg}" for _, pkg in need]
    return None


def wire_commands(which=shutil.which, runner=None):
    """Connect the tools to Claude Code: the graph as an MCP server, and graphify's skill."""
    if not which("claude"):
        return []
    out = []
    if _run(["claude", "mcp", "get", MCP_NAME], runner, timeout=60)[0] != 0:
        out.append(f"claude mcp add --scope user {MCP_NAME} -- code-review-graph serve")
    if not os.path.isdir(os.path.join(os.path.expanduser("~"), ".claude", "skills", "graphify")):
        out.append("graphify install --platform claude")
    return out


ALLOWED = ("uv tool install ", "pipx install ", "claude mcp add --scope user code-review-graph ",
           "graphify install --platform ")


def run_commands(commands, runner=None, write=print):
    """Run each command (only the shapes above), report its exit code, stop at the first failure."""
    for cmd in commands:
        if not cmd.startswith(ALLOWED):
            write(f"Skipped, not a graph tool command: {cmd}")
            return False
        write(f"Running: {cmd}")
        code, out = _run(shlex.split(cmd), runner)
        write(f"Exit code {code}." + (f" {out[-300:]}" if code else ""))
        if code:
            return False
    return True


def repo_root(path):
    code, out = _run(["git", "-C", path, "rev-parse", "--show-toplevel"], timeout=30)
    return out.strip() if code == 0 else None


def build_steps(root, runner=None):
    """The commands that register and build both graphs for one repository."""
    steps = []
    code, out = _run(["code-review-graph", "repos"], runner, timeout=60)
    if code != 0 or root not in out:
        steps.append(["code-review-graph", "register", root, "--alias", os.path.basename(root)])
    steps.append(["code-review-graph", "build", "--repo", root, "-q"])
    steps.append(["graphify", "update", root])
    return steps


def build(path, runner=None, write=print, dry_run=False):
    root = repo_root(path)
    if not root:
        write(f"{path} is not inside a git repository.")
        return 1
    if missing() and not dry_run:
        write("The graph tools are not installed. Run `nodaris-harness graph --install` first.")
        return 1
    for argv in build_steps(root, runner):
        write("Would run: " + shlex.join(argv) if dry_run else "Running: " + shlex.join(argv))
        if dry_run:
            continue
        code, out = _run(argv, runner, cwd=root)
        write(f"Exit code {code}." + (f" {out[-300:]}" if code else ""))
        if code:
            return code
    if not dry_run:
        write(f"Graphs are ready for {root}: the agent can ask code-review-graph about calls and impact, and "
              f"{os.path.join(root, 'graphify-out', 'GRAPH_REPORT.md')} summarises the structure.")
    return 0
