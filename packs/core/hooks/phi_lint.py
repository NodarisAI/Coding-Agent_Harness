#!/usr/bin/env python3
"""PostToolUse hook on writes: catches PHI exposure at the moment the code is written. No model call.

Flags, with the file and line:
  - logging, printing or console output that carries patient fields or raw payloads;
  - exception text interpolated into a log line (exception messages often carry the offending value);
  - realistic identifiers in tests, fixtures and seeds: SSN, phone and email shapes, and a real-looking
    name or date of birth.
Never blocks: it tells the model what to change. Switch off with NODARIS_PHI_LINT=off.
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hlib

SECURITY_BRIEF = "Apply the security procedure to this change. " + hlib.SECURITY_CHECKLIST
FIELDS = (r"patient|member_?id|subscriber|\bssn\b|social_security|\bdob\b|date_of_birth|birth_?date|first_?name|"
          r"last_?name|full_?name|patient_?name|\baddress|street|phone|e_?mail|\bmrn\b|medical_record|diagnos|"
          r"\bicd\b|claim_?body|raw_?(claim|segment|payload|body|content|text|data)|request\.(data|body)|payload|"
          r"segment\.(elements|value|raw)|\.elements\b|insured|guarantor")
LOG_CALL = re.compile(r"\b(logger|logging|log|_log|console|LOG)\.(debug|info|warn|warning|error|exception|critical|log|trace)\s*\(|\bprint\s*\(")
FIELD_RE = re.compile(FIELDS, re.I)
EXC_IN_LOG = re.compile(r"\{\s*(e|exc|err|error|ex)\s*(!r|!s)?\s*\}|str\(\s*(e|exc|err|error|ex)\s*\)|%[sr][^)]*,\s*(e|exc|err|error|ex)\s*\)|repr\(\s*(e|exc|err)\s*\)")
FIXTURE_PATH = re.compile(r"(^|/)(tests?|__tests__|fixtures?|seeds?|factories|mocks?|spec|demo|sample)(/|$)|test_|_test\.|\.test\.|\.spec\.|seed|fixture|factory|mock", re.I)
SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
PHONE = re.compile(r"\(\d{3}\)\s?\d{3}-\d{4}|\b\d{3}[-.]\d{3}[-.]\d{4}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@(?!example\.(com|org|net)\b|test\b|invalid\b|localhost\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
NAME_VALUE = re.compile(r"""(first_?name|last_?name|full_?name|patient_?name|given|family|\bname)["']?\s*[:=]\s*["']([A-Z][a-z]+(?:[ -][A-Z][a-z'-]+)*)["']""", re.I)
DOB_VALUE = re.compile(r"""(dob|birth|born)[\w"']*\s*[:=]\s*["']?(19[2-9]\d|20[01]\d)[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])""", re.I)
FAKE = re.compile(r"test|fake|synthetic|example|sample|demo|dummy|mock|doe\b|alpha|beta|gamma|delta|patient|person|user|zz|xx|placeholder|foo|bar|anon", re.I)


def scan(path, text):
    out = []
    fixture = bool(FIXTURE_PATH.search(path))
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith(("#", "//", "*", "/*")):
            continue
        if LOG_CALL.search(s):
            if FIELD_RE.search(s):
                out.append((i, "a log or print line carries a patient field or raw payload; log counts, kinds and ids of rows you own, never values"))
            elif EXC_IN_LOG.search(s):
                out.append((i, "exception text is interpolated into a log line; exception messages can carry the offending value, so log the error type and a reason code"))
        if fixture:
            if SSN.search(s):
                out.append((i, "an SSN-shaped value in a fixture; use an obviously invalid one such as 000-00-0000"))
            if PHONE.search(s) and not re.search(r"555[-.]?01\d\d|000[-.]000", s):
                out.append((i, "a real-looking phone number in a fixture; use 555-0100 to 555-0199"))
            if EMAIL.search(s):
                out.append((i, "a real-looking email address in a fixture; use example.com"))
            for m in NAME_VALUE.finditer(s):
                if not FAKE.search(m.group(2)):
                    out.append((i, f"a real-looking name ({m.group(2)!r}) in a fixture; use an obviously synthetic one such as 'Test Patient Alpha', or, if the data must look real (a demo), generate it from a seeded synthetic source and mark every record as demo data"))
            if DOB_VALUE.search(s):
                out.append((i, "a plausible date of birth in a fixture; use an obviously synthetic date such as 1900-01-01"))
    return out[:8]


def main():
    if os.environ.get("NODARIS_PHI_LINT", "").lower() == "off":
        return
    data = hlib.read_input()
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not hlib.is_code(path) and not re.search(r"\.(json|ya?ml|csv|sql)$", path):
        return
    text = hlib.written_text(ti)
    findings = scan(path, text)
    notes = []
    sensitive = hlib.is_code(path) and not hlib.is_test(path) and hlib.is_sensitive(path, text)
    with hlib.locked(data.get("session_id", "")) as state:
        if findings:
            hlib.add_event(state, kind="lint", path=path, findings=[m.split(";")[0] for _, m in findings])
        if sensitive and not state.get("security_briefed"):
            state["security_briefed"] = True
            notes.append("Harness path trigger: " + os.path.basename(path) + " is security-sensitive. " + SECURITY_BRIEF)
    if findings:
        body = "; ".join(f"line {n} of the new text: {msg}" for n, msg in findings)
        notes.append(f"Harness PHI lint on {os.path.basename(path)}: {body}. Fix these before continuing.")
    if notes:
        hlib.emit_context("PostToolUse", "\n\n".join(notes))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
