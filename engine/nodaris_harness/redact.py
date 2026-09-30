"""Surrogate redaction: replace every identifier with a realistic stand-in of the same kind and format.

Rules of engagement R-DATA-15/16 and Varun's decision 2 (2026-09-25): a model sees synthetic surrogates, never tags,
and the mapping from real value to surrogate is never written anywhere. So:

- a surrogate keeps the kind and the format (a date stays a date in the same layout, a member id keeps its length
  and its letter/digit pattern, a phone number stays a phone number) so code and parsers still work on the output;
- a surrogate is drawn at random, never computed from the value, so it cannot be reversed or guessed;
- within one run the same value always gets the same surrogate, so records still join up;
- dates move by one random offset per run, which keeps the intervals between them (service to payment, timely
  filing) true while hiding the real dates;
- the mapping lives in memory for one run and is discarded; there is no option to save it.

Detection is the vendored SMCP scanner (phi_guard.py: Safe Harbor regex tier plus X12 structure) plus two passes it
does not cover: labelled identifiers in prose ("member id W123456789", "claim #88213") and the claim, reference,
address and contact elements of an X12 interchange. Anything that cannot be scanned is refused, never passed on.
"""
import datetime, json, os, re, secrets, string, subprocess

from . import phi_guard

# Kinds that make a prompt or a document carry patient information. Dates, URLs and IP addresses alone do not
# (Varun, 2026-09-25 23:44: no PHI policy for dates or URLs); they are still replaced when a document is redacted.
STRONG_KINDS = {"PATIENT_NAME", "SSN", "MRN", "MEMBER_ID", "ACCOUNT", "ACCOUNT_ID", "CLAIM_ID", "PHONE", "FAX", "EMAIL",
                "ADDRESS", "LICENSE", "VEHICLE", "DEVICE", "BIOMETRIC", "PHOTO", "OTHER_ID", "X12_ID", "CONTACT"}
# Contact details identify a person but say nothing about their health. On their own they do not stop a prompt; with a
# health cue, in bulk, or next to a health identifier they do (a teammate's report, 2026-09-30). They are still replaced
# with stand-ins whenever text is redacted, so a model without a BAA never sees them.
CONTACT_KINDS = {"EMAIL", "PHONE", "FAX", "ADDRESS", "ACCOUNT", "ACCOUNT_ID", "CONTACT"}
CONTACT_BULK = 3
HEALTH_CUE = re.compile(
    r"\b(patients?|pt|dob|d\.o\.b|date of birth|born on|diagnos\w*|dx|icd|cpt|hcpcs|claims?|member|mrn|medical|health|"
    r"insurance|insured|payer|eligibility|prescri\w*|medications?|treatments?|clinical|hospital|visits?|encounters?|"
    r"lab results?|conditions?|chart|admission|discharge)\b", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
TEST_DOMAINS = {"example.com", "example.org", "example.net"}
TEST_TLDS = {"test", "example", "invalid", "localhost"}
COMPANY_DOMAINS = {"nodaris.ai"}
_OWN = {}
BINARY_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff",
              ".heic", ".bmp", ".zip", ".gz", ".7z", ".rar", ".dcm", ".sqlite", ".db"}

LABELLED = re.compile(r"\b(member|subscriber|insured|medicaid|medicare|mbi|hicn|account|acct|patient|chart|mrn|"
                      r"medical record|claim|guarantor|beneficiary|policy|check|trace)(\s+(?:id|number|no\.?|num|#))?"
                      r"\s*[:#=]?\s*([A-Z0-9][A-Z0-9-]{4,})\b", re.I)
X12_ELEMENTS = {"CLP": {1: "CLAIM_ID", 7: "CLAIM_ID"}, "REF": {2: "X12_ID"}, "TRN": {2: "CLAIM_ID"},
                "N3": {1: "ADDRESS", 2: "ADDRESS"}, "PER": {4: "CONTACT", 6: "CONTACT", 8: "CONTACT"}, "DMG": {2: "DATE"}}
PERSON_ENTITIES = {"QC", "IL", "74", "QD"}

FIRST = ["Alex", "Jordan", "Casey", "Riley", "Morgan", "Taylor", "Jamie", "Avery", "Quinn", "Rowan", "Harper", "Emerson",
         "Parker", "Sawyer", "Reese", "Dakota", "Hayden", "Kendall", "Logan", "Peyton"]
LAST = ["Alder", "Brook", "Calloway", "Denholm", "Ellery", "Fairbanks", "Garrow", "Hollis", "Ingram", "Jessup",
        "Kestrel", "Lomax", "Marlow", "Norcross", "Oakley", "Pembrook", "Quarry", "Radcliffe", "Stroud", "Thornbury"]
STREETS = ["Maple", "Cedar", "Birch", "Willow", "Lakeview", "Hillcrest", "Ridgeway", "Harbor", "Orchard", "Juniper"]
STREET_WORDS = {"st", "street", "ave", "avenue", "rd", "road", "blvd", "boulevard", "dr", "drive", "ln", "lane", "ct",
                "court", "way", "pl", "place", "suite", "ste", "apt", "unit", "po", "box", "n", "s", "e", "w", "ne", "nw",
                "se", "sw", "hwy", "highway", "pkwy", "parkway", "cir", "circle", "ter", "terrace"}
DATE_FORMATS = [(r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"), (r"\d{2}/\d{2}/\d{4}", "%m/%d/%Y"), (r"\d{1,2}/\d{1,2}/\d{4}", "%m/%d/%Y"),
                (r"\d{2}-\d{2}-\d{4}", "%m-%d-%Y"), (r"\d{8}", "%Y%m%d"), (r"\d{1,2}/\d{1,2}/\d{2}", "%m/%d/%y"),
                (r"[A-Z][a-z]+ \d{1,2}, \d{4}", "%B %d, %Y"), (r"[A-Z][a-z]{2} \d{1,2}, \d{4}", "%b %d, %Y")]


def _rand_like(ch):
    if ch.isdigit():
        return secrets.choice(string.digits)
    if ch.isalpha():
        c = secrets.choice(string.ascii_letters[:26])
        return c.upper() if ch.isupper() else c
    return ch


def _shape(value):
    """Same length, same letter and digit positions, same case, same punctuation; content drawn at random."""
    out = "".join(_rand_like(c) for c in value)
    if value[:1].isdigit() and value[0] != "0" and out[:1] == "0":
        out = secrets.choice("123456789") + out[1:]
    return out


def _case_like(word, model):
    if model.isupper():
        return word.upper()
    if model.islower():
        return word.lower()
    return word


class Surrogates:
    """One run's value-to-surrogate table. It is never written to disk and is dropped with the object."""

    def __init__(self):
        self._map = {}
        self.date_shift = datetime.timedelta(days=-secrets.choice(range(31, 400)))

    def get(self, kind, value):
        key = re.sub(r"\s+", " ", value.strip().upper())
        if key not in self._map:
            for _ in range(8):
                s = self._make(kind, value)
                if s.strip().upper() != key:
                    break
            self._map[key] = s
        return self._map[key]

    def burn(self):
        self._map.clear()

    def _make(self, kind, value):
        if kind == "PATIENT_NAME":
            return self._name(value)
        if kind == "PATIENT_FIRST":
            return _case_like(secrets.choice(FIRST), value) if len(value.strip()) > 1 else secrets.choice(string.ascii_uppercase)
        if kind == "DATE":
            return self._date(value)
        if kind in ("PHONE", "FAX") or (kind == "CONTACT" and "@" not in value and sum(c.isdigit() for c in value) >= 7):
            return self._phone(value)
        if kind == "EMAIL" or (kind == "CONTACT" and "@" in value):
            return _case_like(f"person.{secrets.token_hex(3)}@example.com", value)
        if kind == "SSN":
            digits = iter("9" + "".join(secrets.choice(string.digits) for _ in range(8)))
            return "".join(next(digits, "0") if c.isdigit() else c for c in value)
        if kind == "ADDRESS":
            return self._address(value)
        if kind == "URL":
            return f"https://example.org/r/{secrets.token_hex(3)}"
        if kind == "IP":
            return f"192.0.2.{secrets.choice(range(1, 255))}"
        if kind == "AGE_OVER_89":
            return "90+"
        if kind in ("BIOMETRIC", "PHOTO"):
            return f"[{kind.lower()} removed]"
        return _shape(value)

    def _name(self, value):
        tokens = re.findall(r"[A-Za-z][A-Za-z'.-]*|[^A-Za-z]+", value)
        words = [i for i, t in enumerate(tokens) if t[0].isalpha()]
        if not words:
            return _shape(value)
        comma = "," in value
        last_idx = words[0] if comma else words[-1]
        out = []
        for i, t in enumerate(tokens):
            if not t[0].isalpha():
                out.append(t)
            elif len(t.rstrip(".")) == 1:
                out.append(secrets.choice(string.ascii_uppercase) + t[1:])
            else:
                out.append(_case_like(secrets.choice(LAST if i == last_idx else FIRST), t))
        return "".join(out)

    def _date(self, value):
        v = value.strip()
        for rx, fmt in DATE_FORMATS:
            if re.fullmatch(rx, v):
                try:
                    d = datetime.datetime.strptime(v, fmt)
                except ValueError:
                    continue
                shifted = (d + self.date_shift).strftime(fmt)
                return value.replace(v, shifted)
        return _shape(value)

    def _phone(self, value):
        n = sum(c.isdigit() for c in value)
        orig = "".join(c for c in value if c.isdigit())
        area = str(secrets.choice([a for a in range(201, 990) if a % 100 != 11 and str(a) != orig[-10:-7]]))
        # The line number must differ from the original's, or a real number's last digits survive in the stand-in.
        line = secrets.choice([f"01{d:02d}" for d in range(100) if f"01{d:02d}" != orig[-4:]])
        core = area + "555" + line
        digits = ("1" + core) if n == 11 else core
        digits = (digits + "".join(secrets.choice(string.digits) for _ in range(max(0, n - len(digits)))))[-n:] if n else ""
        it = iter(digits)
        return "".join(next(it, "0") if c.isdigit() else c for c in value)

    def _address(self, value):
        def word(m):
            w = m.group(0)
            if w.lower().rstrip(".") in STREET_WORDS or (len(w) == 2 and w.isupper()):
                return w
            return _case_like(secrets.choice(STREETS), w)
        return re.sub(r"[A-Za-z][A-Za-z'.]*", word, re.sub(r"\d+", lambda m: _shape(m.group(0)), value))


class Result:
    def __init__(self, text, counts, verdict, coverage, reason=""):
        self.text, self.counts, self.verdict, self.coverage, self.reason = text, counts, verdict, coverage, reason

    @property
    def strong(self):
        return {k: n for k, n in self.counts.items() if k in STRONG_KINDS}

    def blocking(self, text):
        """The identifiers that make this text patient information, by kind and count; empty when it is not.
        Health identifiers always count. Contact details count only with a health cue, in bulk (three or more), or next
        to a health identifier; the person's own address, company domains and reserved test domains never count."""
        found = dict(self.strong)
        allowed = sum(1 for m in EMAIL_RE.finditer(text or "") if _allowed_email(m.group(0), m.group(1)))
        if allowed and "EMAIL" in found:
            found["EMAIL"] -= allowed
            if found["EMAIL"] <= 0:
                del found["EMAIL"]
        health = {k: n for k, n in found.items() if k not in CONTACT_KINDS}
        contact = {k: n for k, n in found.items() if k in CONTACT_KINDS}
        if health or (contact and (HEALTH_CUE.search(text or "") or sum(contact.values()) >= CONTACT_BULK)):
            return found
        return {}

    def report(self):
        """What was replaced, by kind and count. Never a value."""
        found = ", ".join(f"{k} {n}" for k, n in sorted(self.counts.items())) or "nothing"
        return {"verdict": self.verdict, "coverage": self.coverage, "replaced": self.counts, "summary": found,
                "reason": self.reason}


def _own_email():
    """The person's own git email, read once per configuration file."""
    key = os.environ.get("GIT_CONFIG_GLOBAL", "")
    if key not in _OWN:
        try:
            out = subprocess.run(["git", "config", "--global", "--get", "user.email"], capture_output=True, text=True,
                                 timeout=2).stdout.strip().lower()
        except (OSError, subprocess.SubprocessError):
            out = ""
        _OWN[key] = out
    return _OWN[key]


def _company_domains():
    domains = set(COMPANY_DOMAINS)
    try:
        home = os.environ.get("NODARIS_HARNESS_HOME") or os.path.join(os.path.expanduser("~"), ".nodaris-harness")
        with open(os.path.join(home, "settings.json")) as fh:
            extra = json.load(fh).get("company_domains")
        if isinstance(extra, list):
            domains.update(str(d).lower() for d in extra if isinstance(d, str))
    except (OSError, ValueError, AttributeError):
        pass
    return domains


def _allowed_email(address, domain):
    domain = domain.lower()
    if domain in TEST_DOMAINS or domain.rsplit(".", 1)[-1] in TEST_TLDS:
        return True
    if domain in _company_domains() or any(domain.endswith("." + d) for d in _company_domains()):
        return True
    own = _own_email()
    return bool(own) and address.lower() == own


def _labelled_spans(text):
    for m in LABELLED.finditer(text):
        v = m.group(3)
        if len(re.findall(r"\d", v)) < 4:
            continue
        label = m.group(1).lower()
        kind = "CLAIM_ID" if label in ("claim", "check", "trace") else "ACCOUNT_ID" if label in ("account", "acct") else \
            "MRN" if label in ("mrn", "chart", "medical record") else "MEMBER_ID"
        yield m.start(3), m.end(3), kind


NAME = re.compile(r"\b([A-Z][a-z]+(?:-[A-Z][a-z]+)?(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?|[A-Z]{2,}(?:-[A-Z]{2,})?,\s*[A-Z]{2,}(?:\s+[A-Z])?)\b")
NAME_CUE = re.compile(r"\b(dob|d\.o\.b|date of birth|born|patient|pt\.?|member|subscriber|insured|beneficiary|guarantor|"
                      r"mrn|ssn|chart|admitted|discharged|seen by|claim for|mr\.|mrs\.|ms\.)\b", re.I)
NOT_NAMES = {"blue", "cross", "shield", "united", "health", "healthcare", "care", "medicare", "medicaid", "aetna", "cigna",
             "humana", "anthem", "kaiser", "test", "patient", "fixture", "payer", "street", "avenue", "road", "general",
             "hospital", "clinic", "center", "medical", "group", "insurance", "plan", "national", "new", "york", "san",
             "los", "north", "south", "east", "west", "member", "claim", "date", "service", "birth"}


def _name_spans(text):
    """Person names the scanner misses because no "Patient:" label precedes them, when a patient cue is close by."""
    for m in NAME.finditer(text):
        words = {w.lower().strip(".,") for w in re.split(r"[\s,-]+", m.group(1)) if w}
        if words & NOT_NAMES:
            continue
        window = text[max(0, m.start() - 60):m.end() + 60]
        if NAME_CUE.search(window):
            yield m.start(1), m.end(1), "PATIENT_NAME"


def _x12_spans(text):
    """Identifier elements inside a real interchange: a fixed-width ISA header through its IEA trailer."""
    pos = 0
    while True:
        m = re.search(r"(?<![A-Za-z0-9])ISA(?=[^A-Za-z0-9\s])", text[pos:])
        if not m:
            return
        start = pos + m.start()
        el = text[start + 3] if len(text) > start + 3 else ""
        isa = text[start:start + 106]
        if len(isa) < 106 or isa.count(el) != 16 or isa[103] != el:
            pos = start + 3
            continue
        term = isa[105]
        iea = re.search(re.escape(term) + r"\s*IEA" + re.escape(el) + r"[^" + re.escape(term) + r"]*" + re.escape(term), text[start:])
        end = start + (iea.end() if iea else len(text) - start)
        off = start
        for seg in text[start:end].split(term):
            parts, p_off = seg.split(el), off
            offsets = []
            for part in parts:
                offsets.append(p_off)
                p_off += len(part) + 1
            tag = parts[0].strip()
            wanted = dict(X12_ELEMENTS.get(tag, {}))
            if tag == "NM1" and len(parts) > 1 and parts[1].strip() in PERSON_ENTITIES:
                wanted.update({3: "PATIENT_NAME", 4: "PATIENT_FIRST", 5: "PATIENT_FIRST"})
                if len(parts) > 9:
                    wanted[9] = "MEMBER_ID" if len(parts) > 8 and parts[8].strip() == "MI" else "X12_ID"
            for idx, kind in wanted.items():
                if idx < len(parts) and parts[idx].strip():
                    yield offsets[idx], offsets[idx] + len(parts[idx]), kind
            off += len(seg) + len(term)
        pos = end


def redact_text(text, surrogates=None):
    """Return a Result whose text has every detected identifier replaced by a surrogate, or a refusal."""
    own = surrogates is None
    sur = surrogates or Surrogates()
    try:
        if not text:
            return Result(text, {}, "clean", phi_guard.COVERAGE_REGEX_SAFE_HARBOR)
        scan = phi_guard.scan(text, strip=False)
        if scan.coverage == phi_guard.COVERAGE_NOT_SCANNED:
            return Result("", {}, "refused", scan.coverage, "the text could not be scanned (too large, or a detector failed)")
        spans = [(s, e, k, 0) for s, e, k in _x12_spans(text)] + [(s, e, k, 1) for s, e, k in _labelled_spans(text)]
        spans += [(s, e, k, 3) for s, e, k in _name_spans(text)]
        spans += [(f.start, f.end, f.kind, 2) for f in scan.findings]
        chosen = []
        for s, e, k, _ in sorted(spans, key=lambda x: (x[3], -(x[1] - x[0]), x[0])):
            if e > s and all(e <= cs or s >= ce for cs, ce, _ in chosen):
                chosen.append((s, e, k))
        counts, out = {}, text
        for s, e, k in sorted(chosen, reverse=True):
            out = out[:s] + sur.get(k, text[s:e]) + out[e:]
            label = "PATIENT_NAME" if k == "PATIENT_FIRST" else k
            counts[label] = counts.get(label, 0) + 1
        return Result(out, counts, "redacted" if chosen else "clean", scan.coverage)
    except Exception as exc:  # any detector failure refuses; nothing half-redacted is returned
        return Result("", {}, "refused", phi_guard.COVERAGE_NOT_SCANNED, f"redaction failed: {type(exc).__name__}")
    finally:
        if own:
            sur.burn()


def redact_file(path, surrogates=None):
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXT:
        return Result("", {}, "refused", phi_guard.COVERAGE_NOT_SCANNED,
                      f"{ext} files cannot be scanned as text. Convert the file to text first (for a PDF: "
                      f"pdftotext FILE.pdf FILE.txt), check the conversion kept everything, and redact the text file.")
    with open(path, "rb") as fh:
        raw = fh.read()
    if b"\x00" in raw[:8192]:
        return Result("", {}, "refused", phi_guard.COVERAGE_NOT_SCANNED, "the file is binary and cannot be scanned as text")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            return Result("", {}, "refused", phi_guard.COVERAGE_NOT_SCANNED, "the file's text encoding could not be read")
    return redact_text(text, surrogates)
