#!/usr/bin/env python3
"""UserPromptSubmit hook: turns whatever the person typed into a work order. No model call.

- First prompt of a session: a repo card (stack, verify commands, where tests live, guides to read).
- A vague request (no file, symbol or command named) gets the spec-first procedure.
- A request that touches a sensitive surface gets the healthcare security procedure.
Also resets the done gate's per-turn block count. Switch off with NODARIS_INTAKE=off.
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hlib, repocard

SPECIFIC = re.compile(
    r"`[^`]+`|[\w.-]+/[\w./-]+|\b[\w-]+\.(py|tsx?|jsx?|go|rs|sql|md|json|ya?ml|toml|cedar)\b|"
    r"\b[a-z]+_[a-z0-9_]+\b|\b[a-z]+[A-Z][A-Za-z0-9]+\b|\b[A-Z][a-z]+[A-Z][A-Za-z]+\b|"
    r"\b(pytest|npm|pnpm|yarn|manage\.py|git)\b")
SENSITIVE = re.compile(
    r"\b(patient|phi\b|hipaa|health|medical|clinic|provider|member|tenant|customer data|other (users|clinics|practices|customers)|"
    r"auth|login|log ?in|sign ?in|password|session|token|permission|roles?\b|access|admin|"
    r"payment|paid\b|billing|claim|remit|era\b|835|837|denial|eligib|insurance|payer|"
    r"upload|webhook|api key|secret|encrypt|redact|de-?identif|re-?identif|audit|"
    r"ai\b|llm|prompt)", re.I)
CODE_ASK = re.compile(r"\b(add|build|make|fix|change|update|create|implement|refactor|remove|delete|support|allow|"
                      r"harden|secure|speed|clean|migrate|rename|move|write|improve|handle|stop|prevent|bind|wire|hook|let|"
                      r"broken|bug|crash\w*|break\w*|error\w*|fail\w*|leak\w*|doesn'?t work|not working|wrong)\b", re.I)

SPEC_FIRST = (
    "Harness intake: this request describes an outcome, not a spec. Before editing anything, follow the spec-first "
    "skill: (1) find the code that owns this behaviour, its callers and its tests (grep for the words in the request, "
    "then read those ranges); (2) write a short brief in your first message: the precise defect or feature, where it "
    "lives, the observable acceptance checks, and what is out of scope; (3) reuse the repository's existing patterns "
    "and frameworks for this kind of change instead of inventing new ones; (4) if nobody can answer a question, take "
    "the safest reversible option that still delivers the request and record it. Then build test-first.")
SECURITY = (
    "Harness intake: this request touches a sensitive surface (patient data, tenants, auth, money, uploads or model "
    "features). Use the healthcare-domain and self-attack skills for depth. The procedure: " + hlib.SECURITY_CHECKLIST)


def classify(prompt):
    words = len(prompt.split())
    specific = len(SPECIFIC.findall(prompt))
    code_ask = bool(CODE_ASK.search(prompt))
    vague = code_ask and specific == 0 and words < 160
    sensitive = (code_ask or specific > 0) and bool(SENSITIVE.search(prompt))
    return {"vague": vague, "sensitive": sensitive, "code_ask": code_ask}


def main():
    if os.environ.get("NODARIS_INTAKE", "").lower() == "off":
        return
    data = hlib.read_input()
    sid = data.get("session_id", "")
    prompt = data.get("prompt") or ""
    parts = []
    c = classify(prompt)
    with hlib.locked(sid) as state:
        state["blocks"] = 0
        if not state.get("card_shown"):
            parts.append("Harness repo card: " + repocard.render(repocard.detect(data.get("cwd") or os.getcwd())))
            state["card_shown"] = True
        hlib.add_event(state, kind="prompt", **c)
        if c["sensitive"]:
            state["security_briefed"] = True
    if c["vague"]:
        parts.append(SPEC_FIRST)
    if c["code_ask"] and hlib.HARDENING_ASK.search(prompt):
        parts.append(hlib.HARDENING)
    if c["sensitive"]:
        parts.append(SECURITY)
    if parts:
        hlib.emit_context("UserPromptSubmit", "\n\n".join(parts))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
