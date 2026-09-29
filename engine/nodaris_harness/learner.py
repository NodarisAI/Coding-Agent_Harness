"""The learner: turns this person's signals into profile changes, lessons and a report for the owner.

Runs out of band (started in the background at session start when the last run is older than a day, or by hand with
`nodaris-harness learn run`), never inside a turn. What it may apply on its own is decided by rules/learner-policy.json
and enforced by profile.check_field; everything else becomes a line in the owner report. With --agent it asks the host's
own model (headless, redacted text only) to phrase a lesson from a cluster of corrections; without it, it uses the
person's latest wording.
"""
import json, os, re, shutil, subprocess, time

from . import memory, profile, router, signals

SKILL_PLAYBOOK = {"investigate": "investigate", "design": "feature", "ship-review": "ship", "product-film": "media",
                  "self-attack": "security-check", "pentest-readiness": "security-check", "spec-first": "feature",
                  "healthcare-app-blueprint": "new-app", "plain-copy": "feature", "pocock-triage": "bug-fix"}
BRIEF = re.compile(r"\b(shorter|too long|less (words|text)|too (verbose|wordy)|tl;?dr|brief(er)?)\b", re.I)
DETAIL = re.compile(r"\b(more detail|explain (more|why)|elaborate|what does that mean|i don'?t understand)\b", re.I)


def _state_path():
    return os.path.join(profile._dir(), "learner-state.json")


def _state():
    try:
        return json.load(open(_state_path()))
    except (OSError, ValueError):
        return {}


def due(hours=24):
    return time.time() - _state().get("last_run", 0) > hours * 3600


def _clusters(rows, min_overlap=0.4):
    clusters = []
    for r in rows:
        toks = memory._tokens(r.get("text"))
        if not toks:
            continue
        for c in clusters:
            if len(toks & c["tokens"]) / max(1, len(toks | c["tokens"])) >= min_overlap:
                c["rows"].append(r)
                c["tokens"] |= toks
                break
        else:
            clusters.append({"tokens": set(toks), "rows": [r]})
    return clusters


def _agent_lesson(texts):
    """Ask the host's model to phrase one lesson. Redacted excerpts only; any failure falls back to the plain text."""
    exe = shutil.which("claude")
    if not exe:
        return None
    prompt = ("These are a developer's repeated corrections to a coding agent (already redacted). Write one lesson the "
              "agent should follow from now on. Reply with JSON only: {\"when\": \"...\", \"do\": \"...\", \"dont\": \"...\"}. "
              "Each field one short sentence.\n\n" + "\n".join(f"- {t}" for t in texts[:8]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE")}
    env["NODARIS_HARNESS_NO_BG"] = "1"  # the helper session must not start another learner
    try:
        out = subprocess.run([exe, "-p", prompt, "--model", "sonnet", "--output-format", "text"], capture_output=True,
                             text=True, timeout=120, env=env).stdout
        data = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
        return {k: str(data.get(k, ""))[:300] for k in ("when", "do", "dont")} if data.get("do") else None
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None


def replay_keywords(playbook, words, prompts):
    """A learned phrase may only claim prompts no rule routes, and only a small share of those."""
    unrouted = [p for p in prompts if not router.route(p, use_profile=False)["playbook"]]
    if not unrouted:
        return True, "no unrouted prompts to check"
    rx = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)
    share = sum(bool(rx.search(p)) for p in unrouted) / len(unrouted)
    limit = profile.learner_policy()["thresholds"]["keyword_max_share"]
    return share <= limit or len(unrouted) < 10, f"matches {share:.0%} of {len(unrouted)} unrouted prompts (limit {limit:.0%})"


def run(use_agent=False, cwd=None):
    th = profile.learner_policy()["thresholds"]
    state = _state()
    rows = signals.load(since=0.0)
    applied, report = [], []

    corrections = [r for r in rows if r["kind"] == "correction" and r.get("text")]
    brief = [r for r in corrections if BRIEF.search(r["text"])]
    detail = [r for r in corrections if DETAIL.search(r["text"])]
    for style, group in (("brief", brief), ("detailed", detail)):
        if len(group) >= th["correction_count"] and len({r["session"] for r in group}) >= th["correction_sessions"]:
            cid = profile.apply_change("style.brevity", style, [len(group)], f"asked for {style} answers {len(group)} times")
            if cid:
                applied.append(cid)

    for c in _clusters([r for r in corrections if r not in brief and r not in detail]):
        sessions = {r["session"] for r in c["rows"]}
        if len(c["rows"]) < th["correction_count"] or len(sessions) < th["correction_sessions"]:
            continue
        texts = [r["text"] for r in c["rows"]]
        phrased = _agent_lesson(texts) if use_agent else None
        when = (phrased or {}).get("when") or f"Working on: {' '.join(sorted(c['tokens'])[:6])}"
        do = (phrased or {}).get("do") or f"The person has corrected this {len(texts)} times. Their latest words: {texts[-1]}"
        lesson, _ = memory.add(cwd or os.getcwd(), when, do, dont=(phrased or {}).get("dont", ""),
                               why=f"learned from {len(texts)} corrections in {len(sessions)} sessions",
                               keywords=sorted(c["tokens"])[:8], scope="user")
        cid = profile.apply_change("lessons", lesson["id"], [len(texts), len(sessions)],
                                   f"new lesson from {len(texts)} repeated corrections: {do[:90]}")
        if cid:
            applied.append(cid)

    unrouted = [r for r in rows if r["kind"] == "unrouted" and r.get("text")]
    skills = [r for r in rows if r["kind"] == "skill"]
    by_pb = {}
    for u in unrouted:
        nxt = next((s for s in skills if s["session"] == u["session"] and s["ts"] > u["ts"]), None)
        pb = SKILL_PLAYBOOK.get((nxt or {}).get("name"))
        if pb:
            by_pb.setdefault(pb, []).append(u["text"])
    all_prompts = [r["text"] for r in unrouted] + [r.get("text", "") for r in rows if r["kind"] == "prompt"]
    for pb, texts in by_pb.items():
        counts = {}
        for t in texts:
            for w in memory._tokens(t):
                counts[w] = counts.get(w, 0) + 1
        words = sorted(w for w, n in counts.items() if n >= th["keyword_prompts"])[:5]
        if not words:
            continue
        ok, why = replay_keywords(pb, words, all_prompts)
        if ok:
            cid = profile.apply_change(f"router_keywords:{pb}", words, [len(texts)], f"'{', '.join(words)}' led to {pb} work {len(texts)} times")
            if cid:
                applied.append(cid)
        else:
            report.append(f"Router phrase {words} for {pb} not applied: {why}.")

    gates = {}
    for r in rows:
        if r["kind"] == "gate":
            gates[r.get("rule")] = gates.get(r.get("rule"), 0) + 1
    for rule, n in sorted(gates.items(), key=lambda x: -x[1]):
        if n >= th["gate_report"]:
            report.append(f"The {rule} check fired {n} times. The learner never changes a check; the owner decides whether it should.")

    state.update(last_run=time.time(), last_applied=applied, last_report=report)
    json.dump(state, open(_state_path(), "w"))
    if report:
        with open(os.path.join(profile._dir(), "owner-report.md"), "w") as fh:
            fh.write("# Learner report for the owner\n\n" + "\n".join(f"- {x}" for x in report) + "\n")
    signals.record("learner", "learner", applied=applied, report=len(report))
    return {"applied": applied, "report": report}


def start_in_background(cli):
    """Called at session start: run the learner detached when it is due, so nobody has to remember to."""
    if os.environ.get("NODARIS_HARNESS_NO_BG") or not due():
        return False
    try:
        subprocess.Popen([cli, "learn", "run", "--agent", "--quiet"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False
