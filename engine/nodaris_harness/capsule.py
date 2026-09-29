"""Keep subagents inside the orchestrator's frame, and keep their token use visible and bounded.

Three pieces, all driven by hooks so nobody has to remember them:

- Capsule: every Task/Agent launch gets a short block appended to its prompt: the session's route, the design
  records and design-system files the work must follow, the repository rules, and the reporting contract. The
  subagent reads the files itself; the capsule carries paths, never file contents.
- Checkpoint: when a subagent returns, the orchestrator is told what it cost, the session total against the budget,
  and what to check before accepting the work. Past the budget, the next launch is refused with instructions, and
  the orchestrator takes the remaining work over itself.
- Tool-call pulse: inside a running subagent, every PULSE tool calls it is reminded of its brief, and at the cap it is
  told to stop and hand back what it has.

The budget comes from settings.json (`subagent_budget_tokens`, set by plan during onboarding); the person can raise
it at any time with `nodaris-harness budget --add N`.
"""
import glob, json, os, re, subprocess, time

from . import gates, policy

MARK = "[Orchestrator capsule]"
PLAN_BUDGET = {"pro": 150_000, "max": 600_000, "team": 400_000, "api": 300_000}
DEFAULT_BUDGET = 300_000
PULSE = 25
TOOL_CAP = 90
DESIGN_SYSTEM = ["DESIGN.md", "DESIGN-SYSTEM.md", "docs/design-system*.md", "docs/DESIGN*.md", ".planning/COPY-STANDARD.md",
                 ".planning/DESIGN*.md", "design-tokens.json", "tokens.json", "tailwind.config.*", "src/styles/tokens*",
                 "src/theme.*", "apps/*/src/styles/tokens*", "packages/*/src/tokens*"]
RULE_FILES = ["CLAUDE.md", "AGENTS.md", ".planning/STATE.md", "CONTRIBUTING.md"]


def _settings():
    try:
        with open(os.path.join(policy.home(), "settings.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def budget():
    s = _settings()
    extra = int(s.get("subagent_budget_extra") or 0)
    base = s.get("subagent_budget_tokens") or PLAN_BUDGET.get(s.get("plan") or "", DEFAULT_BUDGET)
    return int(base) + extra


def _state_path(session):
    return os.path.join(gates._sdir(session), "subagents.json")


def load(session):
    return gates._read(_state_path(session), {"runs": [], "spent": 0, "calls": {}})


def _save(session, state):
    gates._write(_state_path(session), state)


def _existing(cwd, patterns, limit):
    found = []
    for pat in patterns:
        for p in sorted(glob.glob(os.path.join(cwd, pat)))[:2]:
            if os.path.isfile(p) and p not in found:
                found.append(p)
    return [os.path.relpath(p, cwd) for p in found[:limit]]


def build(session, cwd):
    """The capsule text: paths and rules only, under about 1,400 characters."""
    route = gates.load_route(session)
    lines = [MARK, "You are working for an orchestrating agent. Stay inside its frame:"]
    if route.get("playbook"):
        over = ", ".join(route.get("overlays") or []) or "none"
        lines.append(f"- Kind of work: {route['playbook']} (sensitive areas: {over}).")
    designs = [os.path.relpath(p, cwd) for p in gates.design_records(cwd, route.get("started", time.time()))][-2:]
    if designs:
        lines.append("- Follow the design record: " + ", ".join(designs) + ". Do not change its decisions; report a conflict instead.")
    ds = _existing(cwd, DESIGN_SYSTEM, 5)
    if ds:
        lines.append("- Read and follow the design system before touching any UI: " + ", ".join(ds) + ".")
    rules = _existing(cwd, RULE_FILES, 3)
    if rules:
        lines.append("- Repository rules: " + ", ".join(rules) + ".")
    left = max(0, budget() - load(session).get("spent", 0))
    lines += [
        "- Change only the files your task names. Do not commit, push, install packages or switch branches.",
        "- Product text is professional English in sentence case; data in examples and tests is synthetic.",
        f"- Work in small steps. At about {PULSE} tool calls, check you are still on the brief; at {TOOL_CAP} you will be "
        f"stopped. The session's subagent budget has about {left:,} tokens left.",
        "- Finish with: the files you changed, the checks you ran with their exit codes, and what is not done.",
    ]
    return "\n".join(lines)


def _prompt_key(tool_input):
    for k in ("prompt", "description", "task"):
        if isinstance(tool_input.get(k), str):
            return k
    return None


def before_launch(ev):
    """Outcome for a Task/Agent launch from the orchestrator: refuse past the budget, else append the capsule."""
    session, ti = ev["session_id"], dict(ev.get("tool_input") or {})
    spent, cap = load(session).get("spent", 0), budget()
    if spent >= cap:
        return {"decision": "deny", "rule": "subagent-budget",
                "reason": (f"The subagent budget for this session is used up ({spent:,} of {cap:,} tokens). Take the "
                           f"remaining work over in this session, or ask the person whether to raise it; they can run "
                           f"`nodaris-harness budget --add 200000`.")}
    key = _prompt_key(ti)
    if not key or MARK in ti[key]:
        return {"decision": "allow"}
    ti[key] = ti[key].rstrip() + "\n\n" + build(session, ev.get("cwd") or ".")
    return {"decision": "allow", "updated_input": ti}


def _changed_files(cwd):
    try:
        r = subprocess.run(["git", "-C", cwd, "status", "--porcelain"], capture_output=True, text=True, timeout=10)
        return [l[3:] for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def after_return(ev):
    """Checkpoint text after a subagent returns, and the running total."""
    session, ti = ev["session_id"], ev.get("tool_input") or {}
    resp = ev.get("tool_response") if isinstance(ev.get("tool_response"), dict) else {}
    tokens = int(resp.get("totalTokens") or 0)
    if not tokens and isinstance(resp.get("usage"), dict):
        u = resp["usage"]
        tokens = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens"))
    state = load(session)
    state["spent"] = state.get("spent", 0) + tokens
    name = str(ti.get("description") or ti.get("subagent_type") or "subagent")[:60]
    state["runs"].append({"name": name, "tokens": tokens, "tools": resp.get("totalToolUseCount"),
                          "ms": resp.get("totalDurationMs"), "status": resp.get("status"), "at": time.time()})
    state["runs"] = state["runs"][-200:]
    _save(session, state)
    cap = budget()
    pct = int(100 * state["spent"] / cap) if cap else 0
    changed = _changed_files(ev.get("cwd") or ".")
    ui = [f for f in changed if re.search(r"\.(tsx|jsx|vue|svelte|css|scss|html)$", f)]
    lines = [f"Checkpoint: the subagent \"{name}\" used {tokens:,} tokens. Subagents this session: {state['spent']:,} of "
             f"{cap:,} ({pct}%).",
             "Before accepting its work: read its report, review the diff (`git diff --stat`, then the files), run the "
             "targeted tests, and compare it with the brief and the design record."]
    if changed:
        lines.append(f"The working tree has {len(changed)} changed file(s)" + (f", {len(ui)} of them UI files: open the "
                     f"screens and check them against the design system." if ui else "."))
    lines.append("If it drifted from the brief or the design system, fix it in this session rather than sending it back "
                 "more than once.")
    if pct >= 90:
        lines.append("The budget is almost spent: finish the remaining work in this session.")
    elif pct >= 70:
        lines.append("Over 70% of the budget is spent: prefer doing the remaining work in this session.")
    return "\n".join(lines)


def pulse(ev):
    """Inside a subagent: a reminder every PULSE calls and a stop at TOOL_CAP. Returns an outcome or None."""
    agent = ev.get("agent_id")
    if not agent:
        return None
    state = load(ev["session_id"])
    n = state.setdefault("calls", {}).get(agent, 0) + 1
    state["calls"][agent] = n
    _save(ev["session_id"], state)
    if n >= TOOL_CAP:
        return {"decision": "deny", "rule": "subagent-cap",
                "reason": (f"This subagent has made {n} tool calls, the cap for one subagent. Stop now and return what "
                           f"you have: the files you changed, the checks you ran and what remains. The orchestrator "
                           f"will take over.")}
    if n % PULSE == 0:
        return {"decision": "allow", "context": (f"Checkpoint: {n} tool calls so far. Re-read your brief. If the task "
                                                 f"has grown beyond it, stop and report instead of continuing.")}
    return None


def add_budget(n):
    path = os.path.join(policy.home(), "settings.json")
    s = _settings()
    s["subagent_budget_extra"] = int(s.get("subagent_budget_extra") or 0) + int(n)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, path)
    return budget()
