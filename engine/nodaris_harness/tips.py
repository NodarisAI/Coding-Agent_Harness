"""Usage tips from the recorded sessions: patterns that cost time or tokens, each with one concrete change.

Read-only over the redacted episodes. A tip the person dismisses stays quiet for 14 days for the same scope.
"""
import collections, glob, json, os, re, time

from . import policy, trajectory

WINDOW_DAYS = 7
COOLDOWN = 14 * 86400
TEST_CMD = re.compile(r"\b(pytest|npm (run )?test|pnpm test|yarn test|vitest|jest|go test|cargo test|make test|verify\.sh)\b")


def _dismissed_path():
    return os.path.join(policy.home(), "tips-dismissed.json")


def _dismissed():
    try:
        return json.load(open(_dismissed_path()))
    except (OSError, ValueError):
        return {}


def dismiss(key):
    d = _dismissed()
    d[key] = time.time()
    os.makedirs(policy.home(), exist_ok=True)
    json.dump(d, open(_dismissed_path(), "w"))


def _episodes(days):
    cutoff = time.time() - days * 86400
    for path in glob.glob(os.path.join(trajectory.episodes_dir(), "*.jsonl")):
        if os.path.getmtime(path) >= cutoff:
            yield os.path.basename(path)[:8], [s for s in trajectory.load(path) if s.get("ts", 0) >= cutoff]


def compute(days=WINDOW_DAYS):
    tips = []
    blocks, denials = collections.Counter(), collections.Counter()
    for sid, steps in _episodes(days):
        reads, cmds, suites = collections.Counter(), collections.Counter(), 0
        for s in steps:
            inp = s.get("input") or {}
            h = s.get("harness") or {}
            if s.get("event") == "PreToolUse" and s.get("tool") == "Read" and inp.get("file_path"):
                reads[os.path.basename(inp["file_path"])] += 1
            if s.get("event") == "PreToolUse" and s.get("tool") == "Bash" and inp.get("command"):
                cmd = " ".join(inp["command"].split())[:120]
                cmds[cmd] += 1
                suites += bool(TEST_CMD.search(cmd)) and not re.search(r"::|\s-k\s|tests?/\S+\.(py|ts|js)", cmd)
            if h.get("decision") == "block":
                blocks[h.get("rule") or "block"] += 1
            if h.get("decision") == "deny":
                denials[h.get("rule") or "deny"] += 1
        for name, n in reads.items():
            if n >= 6:
                tips.append({"key": f"reread:{sid}:{name}", "size": n,
                             "text": f"{name} was read {n} times in one session. Find the symbol with grep and read that range, "
                                     f"or keep the part you need in the plan instead of rereading the file."})
        for cmd, n in cmds.items():
            if n >= 5:
                tips.append({"key": f"repeat:{sid}:{cmd[:40]}", "size": n,
                             "text": f"The same command ran {n} times in one session: `{cmd[:80]}`. If nothing changed between "
                                     f"runs, the later runs added no information; batch the changes, then run it once."})
        if suites >= 4:
            tips.append({"key": f"suites:{sid}", "size": suites,
                         "text": f"The full test suite ran {suites} times in one session. Run the tests for the files you "
                                 f"changed while working and the full suite once at the end."})
    for rule, n in blocks.items():
        if n >= 5:
            tips.append({"key": f"blocks:{rule}", "size": n,
                         "text": f"The {rule} check sent the agent back {n} times this week. Its reason names what it wants; "
                                 f"doing that before finishing (tests after the last edit, evidence in answers) saves the loop."})
    for rule, n in denials.items():
        if n >= 5:
            tips.append({"key": f"denials:{rule}", "size": n,
                         "text": f"{n} calls were refused under {rule} this week. If that work is legitimate and routine, "
                                 f"ask the owner whether the rule should change rather than approving each call."})
    quiet = {k for k, t in _dismissed().items() if time.time() - t < COOLDOWN}
    return sorted((t for t in tips if t["key"] not in quiet), key=lambda t: -t["size"])
