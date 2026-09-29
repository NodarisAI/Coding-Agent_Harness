"""Request router: each prompt is matched to one playbook by rules before work starts.

A person describes what they want in their own words. The router names the kind of work and the overlays that
apply, and the agent opens that playbook and works through its steps. The decision is recorded with the session so
routing decisions and their outcomes become labelled examples later.
"""
import re

TITLES = {"investigate": "Investigation", "bug-fix": "Bug fix", "feature": "Feature", "new-app": "New healthcare app",
          "rcm-data": "RCM data work", "security-check": "Security check", "plan": "Plan", "refactor": "Refactor",
          "perf": "Performance", "ship": "Ship", "media": "Video and motion"}

RULES = [
    ("ship", r"\b(ship|land|merge|open (a |the )?pr|pull request|release|deploy|get it green|babysit)\b"),
    ("security-check", r"\b(security (check|review|assessment|audit)|pen ?test|penetration|attack (my|the|this)|vulnerab|is (it|this) secure|hardening pass)\b"),
    ("new-app", r"\b(new (app|application|service|product|portal)|from scratch|greenfield|build (me )?(a|an) [\w -]{0,40}(app|portal|system|service|tool))\b"),
    # A question is an investigation even when it names an RCM document; the rcm overlay still carries the domain rules.
    ("investigate", r"^\s*(how|why|what|where|when|which|is|are|does|do|can|should|could)\b[^\n]*\?\s*$"),
    ("perf", r"\b(slow|latency|performance|perf\b|speed up|faster|memory (use|leak)|timeout|takes? (too )?long|takes? \d+ ?(ms|s|sec|seconds|minutes?)|too slow)\b"),
    ("investigate", r"\b(explain|walk me through|how does|why (does|did|is|was)|what happens|are we sure)\b"),
    ("bug-fix", r"\b(bug|broken|crash\w*|fail\w*|error\w*|doesn'?t work|not working|wrong|regress\w*|fix|"
                r"resets?|disappears?|stuck|hangs?|freezes?|flickers?|duplicates?|missing|keeps (\w+ing)|stopped \w+ing)\b"),
    ("rcm-data", r"\b(835|837|27[01]|era\b|remit|eob|claim file|x12|edi\b|payer file|charge (entry|file)|posting|reconcil|denial|carc|rarc)\b"),
    ("media", r"\b(video|film|reel|trailer|promo clip|launch clip|explainer|animat\w*|motion design|lottie|gif|3d scene|three\.?js|remotion|hyperframes)\b"),
    ("refactor", r"\b(refactor|rename|extract|inline|dedup\w*|clean ?up|move .* (into|to)|simplif\w*|restructure)\b"),
    ("plan", r"\b(plan|roadmap|phases?|milestones?|break (this|it) down|spec out|design doc|rfc)\b"),
    ("feature", r"\b(add|build|make|create|implement|support|allow|let|enable|change|update|extend|wire|hook)\b"),
]
OVERLAYS = [
    ("autonomous", r"\b(overnight|autonomous\w*|while i'?m (away|asleep|out)|without (asking|checking in)|build (it|the whole thing) (all|end to end)|auto ?mode|/build auto|keep going until)\b"),
    ("rcm", r"\b(835|837|27[01]|era\b|remit|eob|claim file|x12|edi\b|payer file|charge (entry|file)|posting|reconcil\w*|denials?|carc|rarc)\b"),
    ("phi", r"\b(patient|phi\b|hipaa|health record|medical record|member|subscriber|dob|ssn|mrn)\b"),
    ("tenant-money", r"\b(tenant|practice|clinic|customer data|multi-?tenant|payment|paid|billing|money|amount|balance|refund)\b"),
    ("auth", r"\b(auth|login|log ?in|sign ?in|password|session|token|permission|roles?\b|access|admin|mfa|sso)\b"),
    ("hardening", r"\b(bulletproof|robust|harden|resilien|malformed|garbage|edge cases?|weird (input|files?|data))\b"),
    ("model-feature", r"\b(llm|prompt|agent|model output|ai feature|chatbot|assistant)\b"),
]
PLAYBOOKS = {
    "media": [
        "Pick the tool before writing anything: a short film of this product or a website uses the product-film skill; motion inside a web page (scroll, transitions, 3D) uses the project's existing animation library or CSS; a longer programmatic video in a React project may use Remotion; HTML-to-video compositions may use Hyperframes. Say which one and why in one line.",
        "Install nothing new without asking, and pin any version you do install; never run a package with npx or uvx at an unpinned version.",
        "Screens shown in a video carry synthetic data only; check the visible text with `{cli} redact --check` before rendering.",
        "Look at stills of every scene before the full render; fix overflow, collisions and low contrast.",
    ],
    "investigate": [
        "Follow the investigate skill. Restate the question in one sentence and say what would count as an answer.",
        "Find the code that owns it: grep for the words in the question, then read those ranges and their callers; for a subsystem, split it into angles and give each to a read-only subagent.",
        "Run it where possible (a test, a command, a query) and quote the decisive output.",
        "Answer from the evidence with file and line citations, labelling each claim verified, inferred or not verified. If the evidence contradicts the question's premise, say so. The harness sends back an answer without evidence.",
        "Record a lesson if the answer would surprise the next person.",
    ],
    "bug-fix": [
        "Reproduce the defect yourself with a command or a test, and quote the failing output.",
        "Form the candidate causes and rule them out with runtime evidence until one survives. No guessing.",
        "Write the failing test first, then the smallest fix the evidence justifies.",
        "Rerun the repro and the repository's verify command; quote the exit codes.",
        "If the fix crosses a function boundary or a data shape, follow the design skill before implementing.",
        "Summary: what was broken, the cause, the fix, the evidence. Then record the lesson.",
    ],
    "feature": [
        "Write the brief: the behaviour, where it lives, the acceptance checks, what is out of scope.",
        "Name the data shape first, then the smallest change to the existing patterns that delivers it. If the shape is not obvious, or the work touches patient data, money, tenancy or authentication, follow the design skill and write the design record in docs/design/; the harness checks for it.",
        "Test-first: one failing test per acceptance check, then the code, then the verify command.",
        "Search for every reader and writer of anything whose meaning changed; migrate or list each.",
        "Summary with evidence; anything not run goes under Not verified.",
    ],
    "new-app": [
        "Follow the healthcare-app-blueprint skill: decide the stack, then lay down the twelve foundations with their proofs before any feature.",
        "Threat model in writing, naming every file that holds identity, tenancy, money or patient data; follow the design skill for the architecture and write the design record in docs/design/.",
        "One verify command that runs format, lint, types and tests; a synthetic seed; CI.",
        "Only then the first feature, test-first.",
        "Hand over: README, how to run, what is proven, what is not.",
    ],
    "rcm-data": [
        "Identify the exact document (835, 837, ERA, charge file) and read the client's own sample or template before designing.",
        "Parse structurally, never by column position guesses; every element read is named by its segment and position.",
        "Adjustment codes stay paired (group code plus reason code); amounts are never repaired silently, only rejected or flagged.",
        "Fixtures are synthetic and shaped like the real file (envelope, delimiters, variable-width tails).",
        "Test against malformed, truncated and duplicate inputs; typed errors carry a reason code and a location, never a value.",
    ],
    "security-check": [
        "Run `{cli} security check` in the repository and wait for it: it runs every static scanner and, for each host in the signed scope, the live checks the scope allows. Do not ask the person which checks to run.",
        "Threat model of the surface: callers, trust boundaries, three abuse cases, each mapped to a test.",
        "If it reports no signed scope, say once that live checks on dev and staging start after the owner runs `{cli} security scope --env staging=URL` in their own terminal, and carry on with everything else.",
        "Findings log: severity, evidence, location, fix, and the test that proves the fix.",
        "Independent reviewer on the changes; then the trust receipt.",
    ],
    "plan": [
        "State the goal, who it is for, and the observable definition of done.",
        "Vertical slices, each with its acceptance check and its verify command; a slice without one is not a slice.",
        "Name the judgment calls for the owner and the safest default for each.",
        "Order by risk: the slice that could invalidate the plan goes first.",
    ],
    "refactor": [
        "Characterise current behaviour with tests before changing structure.",
        "One transformation at a time; run the verify command after each.",
        "No behaviour change rides along; if one is needed, it is its own step with its own test.",
    ],
    "perf": [
        "Measure first: a reproducible baseline with the exact command and numbers.",
        "Profile, then change the one thing the profile points at.",
        "Measure again with the same command; report before and after side by side.",
    ],
    "ship": [
        "Run the repository's verify command and quote the exit code.",
        "Follow the ship-review skill: an independent review of the exact commit, fixes, then `{cli} review record` so the approval request shows the review state.",
        "Push or merge only through the approval gate; never to a protected branch, never bypassing a hook.",
        "Confirm the landing against the expected commit; do not assume.",
    ],
}
OVERLAY_TEXT = {
    "autonomous": "Autonomous run: do not start building until a spec file exists (docs/specs/<topic>.md: behaviour, acceptance checks, out of scope), written with the spec-first skill from the repository's real code. If the person is present, stop once for their go-ahead on the spec. During the run, stop and ask before anything touching authentication, payments, deletion or patient data; record every other open decision with the safest reversible choice.",
    "rcm": "RCM documents are in scope: work from the client's real file shape, name every element by segment and position, keep adjustment group and reason codes paired, never repair amounts silently, and use synthetic fixtures shaped like the real file.",
    "phi": "Patient data is in scope: fixtures are synthetic, logs carry counts and kinds only, and any real record is redacted before it reaches a model.",
    "tenant-money": "Tenancy or money is in scope: cross-tenant authorization tests and amounts that are never silently repaired.",
    "auth": "Authentication or access is in scope: identity comes from the principal, every layer enforces it, refusals are audited.",
    "hardening": "Hardening pass over bad-input classes: empty, truncated, oversized, wrong delimiters, missing parts, control characters, duplicates and replays.",
    "model-feature": "A model feature is in scope: prompt-injection tests and bounded, fail-closed outputs.",
}


def route(prompt, use_profile=True):
    text = prompt or ""
    playbook = next((name for name, rx in RULES if re.search(rx, text, re.I | re.S)), None)
    if not playbook and use_profile:
        # Phrases learned from this person's own use; consulted only when no rule matched, so they never override one.
        from . import profile
        for name, words in sorted((profile.load().get("router_keywords") or {}).items()):
            if name in PLAYBOOKS and words and re.search(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", text, re.I):
                playbook = name
                break
    overlays = [name for name, rx in OVERLAYS if re.search(rx, text, re.I)]
    return {"playbook": playbook, "overlays": overlays}


HEAVY = {"new-app", "security-check", "ship"}
GATED = {"phi", "tenant-money", "auth"}


def risk(decision):
    """High when patient data, money, tenancy or authentication is in scope; the gates that apply are named up front."""
    name, overlays = decision.get("playbook"), set(decision.get("overlays") or [])
    gates = []
    if name in ("investigate", "bug-fix"):
        gates.append("the answer must carry evidence")
    if name in ("feature", "new-app") and overlays & GATED:
        gates.append("a design record in docs/design/")
    if overlays & GATED:
        gates.append("cross-tenant and refusal tests")
    if name in ("ship",) or overlays & GATED:
        gates.append("an independent review before any push")
    level = "high" if overlays & GATED else ("medium" if name in HEAVY or "rcm" in overlays else "low")
    return level, gates


def brief(decision, cli="nodaris-harness"):
    if not decision["playbook"]:
        return ""
    name = decision["playbook"]
    lines = [f"Harness router: this is {TITLES[name].lower()} work. Open a todo list with these steps first, in order; "
             f"a step you skip stays listed with a one-line reason."]
    lines += [f"{i}. {s.format(cli=cli)}" for i, s in enumerate(PLAYBOOKS[name], 1)]
    for o in decision["overlays"]:
        lines.append("Overlay: " + OVERLAY_TEXT[o])
    level, gates = risk(decision)
    line = f"Risk: {level}."
    if gates:
        line += " Before this is finished: " + "; ".join(gates) + "."
    if level == "high":
        line += " Tell the person this in one sentence before starting, so a large change is not a surprise."
    lines.append(line)
    return "\n".join(lines)
