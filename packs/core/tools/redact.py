#!/usr/bin/env python3
"""Redact PHI from text before it is shared: a log, an error, a pasted 835 fragment, a support ticket.

  python3 redact.py [FILE]          # reads FILE or stdin, writes the redacted text to stdout
  python3 redact.py --check [FILE]  # reports what kinds were found, prints no text

Detection is the SMCP PHI stack (regex tier plus the X12 tier, vendored in phi_guard.py). Each value is
replaced by an irreversible per-run pseudonym such as [name_3fa1c2]; the same value gets the same
pseudonym within one run, so the redacted text still reads coherently. Counts by kind go to stderr and
never include a value. Exit codes: 0 clean or fully redacted, 2 the text could not be scanned (too large or
a tier failed) and nothing was written, 1 usage error.
"""
import hashlib, hmac, os, re, secrets, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phi_guard

# Identifiers the vendored stack reports only inside X12 or not at all: a labelled id in prose ("member id
# W123456789", "claim #88213", "MRN: 00451"), and the claim, payer control and reference numbers in X12.
LABELLED = re.compile(r"\b(member|subscriber|insured|medicaid|medicare|mbi|hicn|account|acct|patient|chart|mrn|"
                      r"medical record|claim|guarantor|beneficiary|policy|check|trace)(\s+(?:id|number|no\.?|num|#))?"
                      r"\s*[:#=]?\s*([A-Z0-9][A-Z0-9-]{4,})\b", re.I)
X12_MASK = {"CLP": (1, 7), "REF": (2,), "TRN": (2,), "NM1": (9,), "N3": (1, 2), "PER": (4, 6, 8), "DMG": (2,)}
X12_KIND = {("CLP", 1): "CLAIM_ID", ("CLP", 7): "CLAIM_ID", ("TRN", 2): "CLAIM_ID", ("N3", 1): "ADDRESS", ("N3", 2): "ADDRESS",
            ("PER", 4): "CONTACT", ("PER", 6): "CONTACT", ("PER", 8): "CONTACT", ("DMG", 2): "DATE"}


class _Pseudo:
    def __init__(self):
        self.salt, self.cache = secrets.token_bytes(32), {}

    def __call__(self, kind, value):
        # The digest depends on the value alone, so one identifier keeps one pseudonym wherever it appears (a log
        # line and an X12 segment); the kind is only the label.
        if (kind, value) not in self.cache:
            self.cache[(kind, value)] = f"[{kind}_{hmac.new(self.salt, value.strip().upper().encode(), hashlib.sha256).hexdigest()[:8]}]"
        return self.cache[(kind, value)]


def extra_pass(text, counts):
    mask = _Pseudo()

    def lab(m):
        if m.group(3).startswith("[") or len(re.findall(r"\d", m.group(3))) < 4:
            return m.group(0)
        kind = "CLAIM_ID" if m.group(1).lower() in ("claim", "check", "trace") else "MEMBER_ID" if m.group(1).lower() != "account" else "ACCOUNT_ID"
        counts[kind] = counts.get(kind, 0) + 1
        return m.group(0)[: m.start(3) - m.start(0)] + mask(kind, m.group(3))
    text = LABELLED.sub(lab, text)
    # X12 only inside a real interchange: an ISA segment of the standard fixed width (106 characters, the element
    # separator repeated at every field boundary), processed up to its IEA trailer, never text that merely says "ISA".
    pos = 0
    while True:
        m = re.search(r"(?<![A-Za-z0-9])ISA(?=[^A-Za-z0-9\s])", text[pos:])
        if not m:
            break
        start = pos + m.start()
        el = text[start + 3] if len(text) > start + 3 else ""
        isa = text[start:start + 106]
        if len(isa) < 106 or isa.count(el) != 16 or isa[103] != el:
            pos = start + 3
            continue
        term = isa[105]
        iea = re.search(re.escape(term) + r"\s*IEA" + re.escape(el) + r"[^" + re.escape(term) + r"]*" + re.escape(term), text[start:])
        end = start + (iea.end() if iea else len(text) - start)
        segs = text[start:end].split(term)
        for n, seg in enumerate(segs):
            parts = seg.split(el)
            tag = parts[0].strip()
            for idx in X12_MASK.get(tag, ()):
                if idx < len(parts) and parts[idx] and not parts[idx].startswith("["):
                    counts["X12_IDENTIFIER"] = counts.get("X12_IDENTIFIER", 0) + 1
                    kind = X12_KIND.get((tag, idx), "X12_ID")
                    if tag == "NM1" and idx == 9:
                        kind = "MEMBER_ID" if len(parts) > 8 and parts[8].strip() == "MI" else "X12_ID"
                    parts[idx] = mask(kind, parts[idx])
            segs[n] = el.join(parts)
        block = term.join(segs)
        text = text[:start] + block + text[end:]
        pos = start + len(block)
    return text


def main(argv):
    check = "--check" in argv
    args = [a for a in argv if a != "--check"]
    if len(args) > 1:
        print(__doc__, file=sys.stderr)
        return 1
    text = open(args[0], encoding="utf-8", errors="replace").read() if args else sys.stdin.read()
    # Identifiers the Safe Harbor scanner has no rule for (labelled ids, X12 elements) are masked first, with one
    # pseudonym per value; the scanner then handles names, dates, phones and the rest, and decides the verdict.
    found = {}
    pre = extra_pass(text, found)
    result = phi_guard.scan(pre, strip=not check)
    for k, n in result.counts_by_kind.items():
        found[k] = found.get(k, 0) + n
    out = result.text if not check else text
    counts = ", ".join(f"{k}: {n}" for k, n in sorted(found.items())) or "none"
    if result.coverage == phi_guard.COVERAGE_NOT_SCANNED or (not check and result.verdict not in ("clean", "masked")):
        print(f"redact: the text could not be fully scanned ({result.coverage}); nothing written", file=sys.stderr)
        return 2
    print(f"redact: {'found' if check else 'redacted'} {counts}", file=sys.stderr)
    if not check:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
