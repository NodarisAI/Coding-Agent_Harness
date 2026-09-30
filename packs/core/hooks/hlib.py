"""Shared helpers for the harness pack hooks: session state, file and command classification.

State is one JSON file per session under $NODARIS_HARNESS_STATE (default: <tmp>/nodaris-harness). Hooks never
call a model and never read file contents beyond what the tool call itself carries.
"""
import json, os, re, sys, tempfile, time

STATE_DIR = os.environ.get("NODARIS_HARNESS_STATE") or os.path.join(tempfile.gettempdir(), "nodaris-harness")

CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".rb", ".php",
            ".cs", ".sql", ".cedar", ".rego", ".sh", ".swift", ".vue", ".svelte"}
SENSITIVE_PATH = re.compile(
    r"auth|login|session|tenant|permission|polic|cedar|rbac|acl|phi|patient|member|reid|re_ident|token|secret|"
    r"crypt|payment|billing|claim|remit|x12|835|837|era\b|eligib|upload|webhook|serializer|views?/|api/|"
    r"middleware|audit|consent|baa", re.I)
SENSITIVE_TEXT = re.compile(
    r"request\.user|tenant_id|tenant\b|patient|\bssn\b|date_of_birth|\bdob\b|member_id|authorization|"
    r"permission_classes|is_authenticated|jwt|bearer|decrypt|encrypt|\bphi\b|claim_id|remittance", re.I)
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$", re.I)
ASSERTS = re.compile(r"\bassert\b|\bexpect\s*\(|assert[A-Z]\w*\(|pytest\.raises|self\.assert|toThrow|rejects\.|\.status_code\s*==")
ATTACK_TEXT = re.compile(
    r"cross[_ -]?tenant|other[_ ]tenant|another[_ ]tenant|foreign[_ ]tenant|idor|bola|forbidden|\b40[13]\b|"
    r"unauthori[sz]ed|unauthenticated|permission[_ ]denied|injection|inject|malicious|hostile|adversar|"
    r"oversiz|too[_ ]large|exceed|forged|tamper|spoof|replay|leak|redact|escalat|"
    # robustness attacks are attacks too (round 6: legitimate malformed-input tests went unrecognised, the gate kept
    # blocking, and the model reworded its tests to get past it)
    r"malformed|truncat|garbage|corrupt|invalid|fuzz|hostile|bad[_ ]input|missing[_ ]segment|control[_ ]char|"
    r"delimiter|overflow|negative|\bnan\b|infinit|duplicate|reject|refus|unicode|encoding|boundary|empty[_ ]input", re.I)
DOC_EXT = (".md", ".rst", ".txt", ".adoc")
THREAT_TEXT = re.compile(r"threat[ -]model|abuse[ -]cases?|attack (surface|vector|tree)|\bSTRIDE\b|trust boundar", re.I)
# The security procedure, given in full wherever a sensitive change is detected, because a skill the model never
# opens teaches nothing (round 5: the harness arm opened no skill and missed the threat model, the identity binding
# and the audit of refused attempts).
HARDENING = (
    "Harness intake: this asks for robustness, so treat it as a hardening pass over every class of bad input, not a "
    "hunt for one bug. For each class decide the behaviour, give it a typed error with a reason code and the location "
    "(never the value), and a test: empty input; truncated input; oversized input and too many records; missing or "
    "wrong delimiters and envelopes; missing required parts; non-numeric, inconsistent or unbalanced amounts; control "
    "characters and odd encodings; duplicates and replays; state leaking from one record or transaction into the next. "
    "Also search the module and its callers for logging of raw input or exception text, which leaks PHI, and for "
    "callers that crash on the new errors.")
HARDENING_ASK = re.compile(r"bulletproof|robust|harden|resilien|crash|messed[ -]up|malformed|garbage|edge[ -]cases?|weird", re.I)
SECURITY_CHECKLIST = (
    "1. Threat model first: a short note (docs/security/<feature>-threat-model.md, or the repository's existing "
    "security or decision log) naming the changed endpoints and files, every caller kind (patient, staff, another "
    "tenant, service account, background job), the trust boundary, and at least three abuse cases, each mapped to "
    "the test that covers it. "
    "2. Identity from the principal: every identity or tenant field that arrives in the request (tenant, practice, "
    "user, viewer or actor ids) is derived from the authenticated principal or rejected when it disagrees; never "
    "trusted as sent. "
    "3. Every layer: enforce in each layer that already exists (view or route, service, queryset, policy engine), "
    "not only the nearest one. "
    "4. Refusals are audited: a denied attempt writes an audit record attributed to the caller's real tenant, with "
    "counts and kinds and no PHI, and a test asserts it. "
    "5. Uniform errors: a refusal does not reveal whether the other tenant, record or agreement exists. "
    "6. Non-human callers: service accounts and background jobs that use the path still work; test one. "
    "7. Bounded input, fail closed with typed errors, no PHI in logs, errors, responses or fixtures. "
    "8. Attack your own change: each abuse case becomes a test that fails on the unsafe version; run the tests, "
    "then the security scan, then the security-reviewer agent on the diff, and fix what they find. The scan is the "
    "harness's tool, not a file in the repository: in the summary call it the harness security scan and give the "
    "command you ran. "
    "The done gate checks the threat model note, the adversarial tests and the scan.")
# A check counts only when the program actually invoked is a test, lint, type or build runner, read per shell
# segment after env assignments and `cd`, never a mention of one inside an echo, a commit message or a grep.
_RUNNER = re.compile(
    r"^(?:(?:\S*/)?python[\d.]*\s+-m\s+(?:pytest|unittest|mypy|ruff)\b|(?:\S*/)?(?:pytest|mypy|ruff|tox|tsc|vitest|jest|eslint)\b|"
    r"(?:\S*/)?python[\d.]*\s+manage\.py\s+(?:test|check|makemigrations\s+--check)\b|"
    r"npx\s+(?:--no-install\s+)?(?:vitest|jest|tsc|eslint|playwright\s+test)\b|"
    r"(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|verify|check|lint|typecheck|build)\b|"
    r"make\s+(?:test|check|verify|lint)\b|go\s+test\b|cargo\s+test\b|(?:bash\s+|sh\s+|\./)?\S*verify\.sh\b)")
_TEST_RUNNER = re.compile(r"pytest|unittest|manage\.py\s+test|vitest|jest|playwright|(?:npm|pnpm|yarn)\s+(?:run\s+)?test|make\s+test|go\s+test|cargo\s+test|verify\.sh")
_FAIL_OUT = re.compile(r"\b\d+ (?:failed|errors?)\b|^FAILED |^ERROR |error TS\d+|Tests?\s+\d+ failed|✗|\bFAIL\b", re.M)
_PASS_OUT = re.compile(r"\b\d+ passed\b|Tests\s+\d+ passed|✓|\bOK\b|\bPASS\b|^\.+\s*(\[\s*\d+%\])?$", re.M)


SCAN_CMD = re.compile(r"(?:^|[\s;&|/])scan\.py\b")


def verify_segments(cmd):
    """The shell segments of cmd that invoke a check runner."""
    out = []
    for seg in re.split(r"&&|\|\||;|\n", cmd or ""):
        s = seg.strip().split("|")[0].strip()
        s = re.sub(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+", "", s)
        if re.match(r"^(cd|pushd)\s", s):
            continue
        if _RUNNER.match(s):
            out.append(s)
    return out


def is_test_run(cmd):
    return any(_TEST_RUNNER.search(s) for s in verify_segments(cmd))


def output_verdict(stdout):
    """'fail' when the output shows failures (a pipe such as `| tail` hides the exit code), 'pass' when it shows
    tests passing, else 'unknown'."""
    if _FAIL_OUT.search(stdout or ""):
        return "fail"
    return "pass" if _PASS_OUT.search(stdout or "") else "unknown"


def read_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return os.path.join(STATE_DIR, safe + ".json")


def load(session_id):
    try:
        with open(_path(session_id)) as f:
            return json.load(f)
    except Exception:
        return {"seq": 0, "events": [], "blocks": 0, "card_shown": False}


def save(session_id, state):
    os.makedirs(STATE_DIR, exist_ok=True)
    p = _path(session_id)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, p)


class locked:
    """Serialise read-modify-write of one session's state across concurrent hook processes."""

    def __init__(self, session_id):
        self.sid = session_id

    def __enter__(self):
        import fcntl
        os.makedirs(STATE_DIR, exist_ok=True)
        self.fh = open(_path(self.sid) + ".lock", "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        self.state = load(self.sid)
        return self.state

    def __exit__(self, *exc):
        import fcntl
        try:
            if exc[0] is None:
                save(self.sid, self.state)
        finally:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()


def add_event(state, **ev):
    state["seq"] = state.get("seq", 0) + 1
    ev["seq"] = state["seq"]
    ev["t"] = round(time.time(), 1)
    state.setdefault("events", []).append(ev)
    del state["events"][:-400]
    return ev


def git_root(path):
    d = os.path.abspath(path or ".")
    while d and d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return ""


def is_code(path):
    return os.path.splitext(path or "")[1].lower() in CODE_EXT


def is_test(path):
    return bool(TEST_PATH.search(path or ""))


def is_sensitive(path, text=""):
    return bool(SENSITIVE_PATH.search(path or "")) or bool(SENSITIVE_TEXT.search(text or ""))


def written_text(tool_input):
    """The text a Write, Edit, MultiEdit or NotebookEdit call adds."""
    ti = tool_input or {}
    parts = [ti.get("content") or "", ti.get("new_string") or "", ti.get("new_source") or ""]
    for e in ti.get("edits") or []:
        parts.append((e or {}).get("new_string") or "")
    return "\n".join(p for p in parts if p)


def emit_context(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
