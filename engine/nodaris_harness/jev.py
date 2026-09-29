"""Jev: a small decision model that reads each prompt and tells the agent how to shape its reply.

Jev (typesafe/jev-1.13 on OpenRouter) answers a few typed questions about the message: what kind of work it is,
how much effort it deserves, whether it is security or verification work, and whether the reply should be brief,
explained or taught. The answers are added to the agent's context as two short lines.

Privacy: the message is checked for patient identifiers first. A message with a strong identifier, or one that
cannot be checked, is never sent. Weaker identifiers are replaced with stand-ins and credentials are masked before
anything leaves the machine. Jev is best effort: with no key, no network or a spent daily budget it adds nothing.

The key is the Nodaris team key, fetched once from AWS Secrets Manager with the person's own AWS login
(`nodaris-harness jev fetch-key`) and kept in a file only they can read. It is never printed or committed.
"""
import datetime, http.client, json, os, re, subprocess, threading, time
from urllib.parse import urlsplit

from . import policy, redact

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
SECRET_ID = "nodaris/harness/jev"
DAILY_CAP_USD = 0.50
BUDGET_S = 3.5
ATTEMPT_S = 1.6
RETRY_STATUS = {408, 429, 500, 502, 503, 504, 524, 529}
MIN_WORDS, MAX_CHARS = 6, 16000

TYPES = {
    "build": "Create or extend software, features, pages, scripts, docs or content.",
    "fix": "Debug or repair something broken, failing or wrong.",
    "research": "Investigate, compare, look up or analyse information.",
    "deploy": "Release, ship, push, promote or configure an environment.",
    "design": "Visual, UX, product or system design decisions.",
    "ops": "Operate tools, accounts, data, files, schedules or infrastructure chores.",
    "question": "Answer or explain something without changing anything.",
    "other": "None of these.",
}
EFFORTS = {
    "low": "Routine: a question, lookup, small edit or one clear change. The usual choice.",
    "medium": "Several steps or files: a normal build, fix, deploy or research task.",
    "high": "Architecture, security or verification work that needs careful judgment.",
    "max": "Large, correctness-critical architecture, security or verification work.",
}
SHAPES = {
    "brief": "A short answer or confirmation: the request is clear and routine, and one or two sentences settle it.",
    "explain": "Plain-language explanation: what happened or what the answer is, why it matters, and what it means "
               "for the reader, in a few short paragraphs without jargon or fragments.",
    "teach": "Step-by-step walkthrough: the reader is confused, frustrated, asks how or why something works, or is "
             "deciding something new, so the reply should build understanding, not just report.",
}
SHAPE_TEXT = {
    "brief": "brief (one or two plain sentences; no recap)",
    "explain": "explain (plain paragraphs: what happened, why it matters, what it means for the reader; no recap of earlier turns)",
    "teach": "teach (step by step in plain language, define any term the reader may not know, then the decision or action)",
}

_SECRETS = [re.compile(p) for p in (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b",
    r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b",
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}",
    r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S{6,}",
    r"://[^/\s:@]+:[^/\s@]+@",
    r"\b[A-Fa-f0-9]{32,}\b",
    r"\b[A-Za-z0-9+_-]{40,}={0,2}",
)]


def _dir():
    d = os.path.join(policy.home(), "jev")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def key_path():
    return os.path.join(policy.home(), "secrets", "jev.key")


def _key():
    try:
        with open(key_path()) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def is_off():
    return os.environ.get("NODARIS_JEV", "").lower() in ("0", "off", "false", "no") or \
        os.path.exists(os.path.join(policy.home(), "jev", "off"))


def set_enabled(on):
    flag = os.path.join(_dir(), "off")
    if on:
        try:
            os.remove(flag)
        except OSError:
            pass
    else:
        open(flag, "w").close()


def mask_secrets(text):
    n = 0
    for rx in _SECRETS:
        def sub(_m):
            nonlocal n
            n += 1
            return f"[SECRET-{n}]"
        text = rx.sub(sub, text)
    return text, n


def fetch_key(profile=None, secret_id=SECRET_ID, runner=subprocess.run):
    """Read the team key from AWS Secrets Manager with the person's own AWS login and store it privately.
    Returns (ok, message). The key itself is never returned, printed or logged."""
    cmd = ["aws", "secretsmanager", "get-secret-value", "--secret-id", secret_id,
           "--query", "SecretString", "--output", "text"] + (["--profile", profile] if profile else [])
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, ("The AWS command line tool is not installed. Install it (on a Mac: `brew install awscli`), "
                       "sign in with `aws sso login`, then run `nodaris-harness jev fetch-key`.")
    except subprocess.TimeoutExpired:
        return False, "AWS did not answer within a minute. Check your network and run `nodaris-harness jev fetch-key` again."
    if p.returncode != 0:
        first = (p.stderr or "").strip().splitlines()[-1:] or ["no error text"]
        first = mask_secrets(first[0])[0][:240]
        if "AccessDenied" in first or "not authorized" in first:
            why = "Your AWS login cannot read the team key. Ask a Nodaris admin to grant you read access to " + secret_id + "."
        elif "ResourceNotFound" in first:
            why = "The team key " + secret_id + " does not exist in this AWS account and region yet. Ask a Nodaris admin."
        elif "Unable to locate credentials" in first or "Token has expired" in first or "sso" in first.lower():
            why = "You are not signed in to AWS. Run `aws sso login` (or `aws configure`), then `nodaris-harness jev fetch-key`."
        else:
            why = "AWS refused the request: " + first
        return False, why
    value = (p.stdout or "").strip()
    if value.startswith("{"):
        try:
            obj = json.loads(value)
            value = obj.get("OPENROUTER_API_KEY") or obj.get("key") if isinstance(obj, dict) else ""
            value = value if isinstance(value, str) else ""
        except ValueError:
            value = ""
    if not value or "\n" in value or len(value) < 20:
        return False, "The team key in AWS is empty or malformed. Ask a Nodaris admin to check " + secret_id + "."
    path = key_path()
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        os.chmod(os.path.dirname(path), 0o700)
        if os.path.lexists(tmp):
            os.remove(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(value + "\n")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, "The team key could not be saved in %s (%s). Check that folder, then run `nodaris-harness jev fetch-key` again." % (
            os.path.dirname(path), type(exc).__name__)
    try:
        os.remove(_auth_flag())
    except OSError:
        pass
    return True, "The team key is saved in " + path + ", readable only by you."


def status():
    key = _key()
    spent = _spent_today()
    return {"enabled": not is_off() and _chosen(), "key": bool(key), "key_path": key_path(),
            "key_refused": os.path.exists(_auth_flag()), "spent_today_usd": round(spent, 4), "daily_cap_usd": DAILY_CAP_USD}


def _ledger():
    return os.path.join(_dir(), "calls-" + datetime.date.today().isoformat() + ".jsonl")


def _spent_today():
    total = 0.0
    try:
        with open(_ledger()) as fh:
            for ln in fh:
                try:
                    total += float(json.loads(ln).get("cost") or 0)
                except (ValueError, TypeError):
                    pass
    except OSError:
        pass
    return total


def _recent_failures(window=180):
    """Failures in a row at the end of today's ledger, counting only those inside the window."""
    now, recs = time.time(), []
    try:
        with open(_ledger()) as fh:
            for ln in fh:
                try:
                    recs.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        pass
    n = 0
    for rec in reversed(recs):
        if rec.get("ok") or now - float(rec.get("t") or 0) >= window:
            break
        n += 1
    return n


def _auth_flag():
    return os.path.join(_dir(), "key-refused")


def _log(rec):
    try:
        with open(_ledger(), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def questions(first):
    q = {
        "type": {"type": "choice", "instructions": "What kind of work does the message mainly ask for?",
                 "criteria": dict(TYPES)},
        "effort": {"type": "choice", "instructions": (
            "How much effort does the task deserve? Most tasks are low or medium; high and max are only for "
            "architecture, security or verification work."), "criteria": dict(EFFORTS)},
        "shape_reply": {"type": "choice", "instructions": (
            "How should the assistant's final reply to this message be shaped for a reader who wants plain "
            "language and dislikes both jargon fragments and repeated recaps?"), "criteria": dict(SHAPES)},
        "critical": {"type": "noul", "instructions": (
            "Is this architecture, security or verification work: system design, auth, secrets, access control, "
            "data protection, compliance, or proving something correct?"),
            "criteria": {"true": "Yes, mistakes here are costly and need the strongest reasoning.",
                         "false": "No, ordinary product or operations work."}},
    }
    if not first:
        q["reply"] = {"type": "noul", "instructions": (
            "Is the message mainly a reply to the assistant's previous turn (approving it, answering its question, "
            "or adjusting its last proposal) rather than a new request?"),
            "criteria": {"true": "A reply that depends on the previous turn.",
                         "false": "A new or self-contained request."}}
    return q


class _HttpFail(Exception):
    def __init__(self, status):
        super().__init__("http %s" % status)
        self.status = status


def _post(url, payload, key, timeout):
    u = urlsplit(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname, u.port, timeout=max(0.2, timeout))
    try:
        conn.request("POST", u.path or "/", body=json.dumps(payload).encode(), headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json", "X-Title": "nodaris-harness"})
        resp = conn.getresponse()
        data = resp.read()
        if resp.status != 200:
            raise _HttpFail(resp.status)
    finally:
        conn.close()
    return json.loads(data)


def parse(data, qs):
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise ValueError("no answers")
    out = {}
    for qid, q in qs.items():
        a = answers.get(qid)
        if not isinstance(a, dict):
            continue
        if q["type"] == "noul":
            p = a.get("noul")
            if isinstance(p, (int, float)) and 0 <= p <= 1:
                out[qid] = float(p)
        elif q["type"] == "choice":
            c = a.get("choice")
            if isinstance(c, str) and c in q["criteria"]:
                probs = a.get("probabilities") if isinstance(a.get("probabilities"), dict) else {}
                p = probs.get(c, a.get("confidence"))
                out[qid] = (c, float(p) if isinstance(p, (int, float)) else 0.0)
    return out


def _cost(data, in_chars):
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict) and isinstance(usage.get("cost"), (int, float)):
        return float(usage["cost"])
    tin = (usage or {}).get("prompt_tokens") or in_chars / 4
    return float(tin) * 1.0 / 1e6


def _endpoint(url=None):
    """The team key goes only to OpenRouter over HTTPS; an override is honoured only for a loopback test server."""
    url = url or os.environ.get("NODARIS_JEV_URL") or URL
    u = urlsplit(url)
    if u.scheme == "https" and u.hostname == "openrouter.ai":
        return url
    if u.scheme in ("http", "https") and u.hostname in ("127.0.0.1", "localhost", "::1"):
        return url
    return URL


def ask(text, first, key=None, url=None):
    """Jev's answers for a message that already passed the privacy checks, or None."""
    key = key or _key()
    if not key:
        return None
    qs = questions(first)
    body = {"model": MODEL, "state": {"message": text, "first_message_in_session": bool(first)}, "questions": qs}
    raw = json.dumps(body)
    if key in raw:
        return None
    url = _endpoint(url)
    started = time.monotonic()
    box = {}

    def call():
        attempts, err = 0, "no time"
        while time.monotonic() < started + BUDGET_S - 0.3 and attempts < 2:
            attempts += 1
            try:
                data = _post(url, body, key, min(ATTEMPT_S, started + BUDGET_S - time.monotonic()))
                box["ans"], box["cost"] = parse(data, qs), _cost(data, len(raw))
                return
            except _HttpFail as exc:
                err = "http %s" % exc.status
                if exc.status in (401, 402, 403):
                    box["refused"] = True
                if exc.status not in RETRY_STATUS or exc.status == 429:
                    break
            except ValueError:
                err = "bad answer"
                break
            except Exception as exc:  # noqa: BLE001  network trouble never holds up the prompt
                err = type(exc).__name__
        box["err"] = err

    # The whole call, DNS included, runs in a thread the prompt waits on for at most the budget.
    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(BUDGET_S)
    ms = int((time.monotonic() - started) * 1000)
    if "ans" in box:
        _log({"t": time.time(), "ok": True, "ms": ms, "cost": box["cost"]})
        return box["ans"]
    if box.get("refused"):
        try:
            open(_auth_flag(), "w").close()
        except OSError:
            pass
    _log({"t": time.time(), "ok": False, "err": box.get("err", "timeout"), "ms": ms, "cost": len(raw) / 4 / 1e6})
    return None


def lines(ans):
    """The context Jev adds: a route line and a reply-shape line, each only when Jev is sure enough."""
    out = []
    if not ans:
        return out
    if ans.get("reply", 0.0) < 0.6 and "type" in ans:
        parts = [ans["type"][0]]
        if "effort" in ans:
            parts.append("effort " + ans["effort"][0])
        if ans.get("critical", 0.0) >= 0.5:
            parts.append("keep on the strongest model (architecture, security or verification)")
        out.append("Route: " + " · ".join(parts) + " (a hint; settings are unchanged)")
    if "shape_reply" in ans:
        shape, p = ans["shape_reply"]
        if p >= 0.4 and shape in SHAPE_TEXT:
            out.append("Reply shape: " + SHAPE_TEXT[shape])
    if out:
        out.insert(0, "Jev's reading of the message above (the message and conversation win on any conflict):")
    return out


def _chosen():
    """Jev runs only for a person whose onboarding turned it on."""
    try:
        with open(os.path.join(policy.home(), "settings.json")) as fh:
            return json.load(fh).get("jev") is True
    except (OSError, ValueError, AttributeError):
        return False


_NAME_PAIR = re.compile(r"\b[A-Z][a-z'\-]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'\-]+\b")


def mask_names(text):
    """Replace every capitalised word pair (a possible person's name) with a placeholder. Jev needs the intent of a
    message, not its proper nouns, and the name detector only finds names next to a cue such as 'patient'."""
    return _NAME_PAIR.sub("[NAME]", text)


def for_prompt(ev, text, marker=None):
    """The context lines for one prompt, or an empty string. Never raises and never sends unchecked text.
    marker: a per-session file that records whether Jev has seen a message in this session."""
    try:
        if is_off() or not _chosen() or ev.get("agent_id") or not text:
            return ""
        t = text.strip()
        if t.startswith(("/", "!")) or len(t) > MAX_CHARS or len(t.split()) < MIN_WORDS:
            return ""
        if not _key() or os.path.exists(_auth_flag()) or _spent_today() >= DAILY_CAP_USD or _recent_failures() >= 2:
            return ""
        r = redact.redact_text(t)
        if r.verdict == "refused" or r.strong:
            return ""
        safe, _ = mask_secrets(mask_names(r.text))
        first = not (marker and os.path.exists(marker))
        if marker and first:
            try:
                open(marker, "w").close()
            except OSError:
                pass
        return "\n".join(lines(ask(safe, first)))
    except Exception:  # noqa: BLE001
        return ""
