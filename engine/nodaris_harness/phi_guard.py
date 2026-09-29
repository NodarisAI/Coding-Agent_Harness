"""Standalone PHI detection + irreversible masking for the Agent OS.

A dependency-light port of the Nodaris SMCP PHI stack
(``smcp/phi/regex_v2.py``, ``smcp/phi/scanner_v2.py``, ``smcp/phi/deidentify.py``,
``smcp/phi/deid_map.py``, ``smcp/phi/fp_rules.py``) into a single stdlib-only
module. Agent OS processes uploaded EDI/CSV bundles in RAM and keeps **no PHI
at rest**, so the reversible-tokenization half of SMCP is deliberately absent.

THE ONE RULE CARRIED OVER VERBATIM
----------------------------------
**A finding never carries the matched substring.** ``PhiFinding`` has offsets,
a category, a kind, a tier and the substituted mask — and nothing else. Nothing
in this module logs, returns, or embeds the original value. The raw span is read
in-process from the caller's own string only long enough to derive an HMAC.

WHAT WAS PORTED FAITHFULLY
--------------------------
* The full 18-category HIPAA Safe-Harbor enum (``PhiCategory``) and every regex
  in SMCP's ``regex_v2.PATTERNS``, in declaration order, with declaration order
  preserved as a tie-break so earlier (more specific) patterns still win.
* ``deidentify._select_non_overlapping``: earliest-start / widest-span /
  lowest-tier-rank wins, and a character is masked at most once.
* Reverse-order splice — substitutions applied from the highest offset down so
  earlier offsets stay valid while the text length changes underneath.
* ``_TIER_RANK`` — a precise structural/regex Safe-Harbor hit beats a fuzzier
  tier on the same span.
* ``deid_map.category_to_kind`` — total over the closed enum, raising on an
  unmapped category (fail loud, never silently mis-label), and returning ``None``
  for the age>89 *generalization* obligation, which becomes ``"90+"`` rather
  than a pseudonym.
* ``scanner_v2._suppress_false_positives`` — surrogate UUIDs, CPT/HCPCS codes,
  bare-10-digit provider NPIs and the scanner's own placeholder tokens are not
  PHI and are never masked.
* The per-request amplification guard (SMCP's ``_MAX_SPANS_PER_REQUEST``) and
  the fail-closed posture on any tier error.

WHAT COULD NOT BE BROUGHT ACROSS — THE HONEST COVERAGE GAP
----------------------------------------------------------
SMCP runs four tiers. This module runs the deterministic ones only:

===================  =========================================================
SMCP tier            Status here
===================  =========================================================
``regex_v2``         Ported in full (plus the X12 tier below).
``presidio_adapter`` **ABSENT.** Needs presidio-analyzer + spaCy
                     ``en_core_web_lg``. Not in ``pyproject.toml`` and not
                     being added.
``jsl_adapter``      **ABSENT.** Needs a licensed John Snow Labs Spark-NLP
                     clinical NER model.
``classifier``       **ABSENT.** Needs a live Claude Haiku call; this module is
                     synchronous, offline and free of network I/O.
===================  =========================================================

The practical consequence is that **free-form person names, place names, ages
and clinical narrative that carry no structural or lexical marker are NOT
detected.** ``"Jane Doe was seen Tuesday"`` in an X12 ``NTE`` segment or a CSV
memo column comes back ``clean``. SMCP's NER tiers existed precisely to catch
that. Every result therefore reports ``coverage == "regex_safe_harbor_only"``
so no caller can mistake a clean verdict for a full-tier clean verdict.
SMCP's disagreement-driven ``needs_human_review`` path is likewise gone: with
one deterministic tier there is nothing to disagree with. ``needs_human_review``
here means "not scanned or not stripped", never "the detectors argued".

Two further residual gaps, both inherited with SMCP's suppression rules and
both genuinely undecidable from the digits alone:

* A **standalone 5-digit number** is a ZIP code and a CPT/HCPCS code in the
  same shape. Outside an X12 ``N4`` element or a prose "City, ST 27601" /
  "ZIP:" context it is kept, on the grounds that masking every procedure code
  would destroy the payment-reconciliation agent's entire output.
* A **bare 10-digit number** is a provider NPI and a US phone number in the
  same shape. Outside an X12 ``PER*TE`` / ``PER*FX`` element or a "phone:"
  label it is kept. Formatted numbers (``919-555-0100``) are always masked.

WHAT WAS ADDED
--------------
An **X12 structural tier** (``tier="x12"``), because the payload is healthcare
EDI and regexes cannot see the segment/element grid. It resolves exactly the
identifiers SMCP relied on NER for in claim files: ``NM1*IL`` / ``NM1*QC``
patient and subscriber names, member and Medicare IDs, ``DMG*D8`` dates of
birth, ``N3``/``N4`` patient street/city/postal, ``PER`` phone/fax/email in a
patient loop, PHI-bearing ``REF`` qualifiers, and patient-linked ``DTP``/``DTM``
dates. It tracks loop subject from the enclosing ``NM1``/``N1`` entity code, so
the *provider's* address and the *payer's* address are left alone. Unknown
entity codes are treated as patient (fail closed).

Two refinements over the SMCP regexes, both deliberate:

1. **Labelled patterns capture only the identifier.** SMCP's ``ssn.labeled9``,
   ``mrn.labeled``, ``cert_license.labeled`` … span the label *and* the value,
   so masking eats the word "SSN". Here the label is preserved and only the
   value is replaced. This also makes ``NPI: 1234567893`` reduce to a bare
   10-digit span, which the ported NPI suppression then correctly spares.
2. **``date.dob_label``** matched the label plus a single digit in SMCP, which
   would have masked ``"DOB: 1"`` and left ``980101`` in the clear. Fixed to
   capture the whole date.

MASKING
-------
Irreversible and per-run. A span becomes ``[KIND_<hex>]`` where ``<hex>`` is the
leading bytes of ``HMAC-SHA256(salt, kind || NUL || value)`` and ``salt`` is 32
random bytes generated at the top of the call and discarded at the end of it.
Consequences, by design:

* Stable **within** one call — the same value yields the same pseudonym, so a
  masked document stays readable and cross-references between an 837 and its
  835 still line up on the same patient, member ID and date of service.
* Unrecoverable **after** the call — the salt is gone, so there is no map, no
  table and no re-identification path. ``scan_bundle`` shares one salt across
  the bundle for the same reason.

``_PSEUDONYM_HEX_LEN`` is 8, not 4: at 4 hex characters two distinct patients
collide onto one pseudonym at roughly 300 values (birthday bound), which would
silently merge two people in the agent's output.

WHAT IS DELIBERATELY NOT MASKED
-------------------------------
Claim identifiers (``CLM01`` / ``CLP01`` / ``CLP07``), CPT/HCPCS procedure
codes, revenue and dollar amounts, ICD-10 codes, provider NPIs, payer IDs,
routing/check numbers and X12 control segments. Masking those does not protect
a patient — it destroys the payment-reconciliation agent's output. The risk this
accepts is stated plainly: ``CLM01`` is a *patient account number* at some
submitters, and this module keeps it.

@source: HIPAA Privacy Rule — 45 CFR 164.514(b)(2) Safe Harbor
@sourceUrl: https://www.hhs.gov/hipaa/for-professionals/privacy/special-topics/de-identification/index.html
@confidence: 90
"""

from __future__ import annotations

import bisect
import hashlib
import hmac
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Coverage label for a completed scan. Deterministic tiers only — see the
#: module docstring for exactly which SMCP tiers are missing and what that
#: means for free-form names and clinical narrative.
COVERAGE_REGEX_SAFE_HARBOR: Final = "regex_safe_harbor_only"

#: Coverage label when nothing was scanned (oversized input or tier failure).
COVERAGE_NOT_SCANNED: Final = "not_scanned"

VERDICT_CLEAN: Final = "clean"
VERDICT_MASKED: Final = "masked"
VERDICT_NEEDS_HUMAN_REVIEW: Final = "needs_human_review"

#: Hard input cap. Above this the scan is refused rather than truncated —
#: a truncated scan would report "clean" for text it never looked at.
# Must exceed the web layer's total upload cap (60 MB), or a normal month of
# remittances is "too large to scan" and the user is pushed to disable the only
# PHI control in the product. The scan is linear (the two catastrophic patterns
# were rewritten 2026-08-11), so this is bounded work, not an open door.
MAX_SCAN_CHARS: Final = 80_000_000

#: Amplification guard, ported from SMCP's ``_MAX_SPANS_PER_REQUEST``. A single
#: request must not fan out to an unbounded number of substitutions.
MAX_FINDINGS: Final = 50_000

#: Length of the HMAC prefix in a pseudonym. See the module docstring.
_PSEUDONYM_HEX_LEN: Final = 8


class PhiCategory(StrEnum):
    """18 HIPAA Safe-Harbor identifier categories (§164.514(b)(2)(i))."""

    NAME = "name"  # (A) Names
    GEO = "geo"  # (B) Geographic subdivisions < state
    DATE = "date"  # (C) Dates other than year
    PHONE = "phone"  # (D) Telephone numbers
    FAX = "fax"  # (E) Fax numbers
    EMAIL = "email"  # (F) Email addresses
    SSN = "ssn"  # (G) Social Security numbers
    MRN = "mrn"  # (H) Medical record numbers
    HEALTH_PLAN = "health_plan"  # (I) Health plan beneficiary numbers
    ACCOUNT = "account"  # (J) Account numbers
    CERT_LICENSE = "cert_license"  # (K) Certificate / license numbers
    VEHICLE = "vehicle"  # (L) Vehicle identifiers / VIN / plate
    DEVICE = "device"  # (M) Device identifiers / serial nums
    URL = "url"  # (N) URLs
    IP = "ip"  # (O) IP addresses
    BIOMETRIC = "biometric"  # (P) Biometric identifiers
    PHOTO = "photo"  # (Q) Full-face / comparable photos
    OTHER_UNIQUE_ID = "other_unique_id"  # (R) Any other unique identifier


# Mask kinds. These are the labels a reader sees in masked output, so they are
# finer-grained than SMCP's tokenization kinds — SMCP had to collapse FAX into
# PHONE and DATE/ACCOUNT/… into ``other_identifier`` because the kind drove
# re-identification and had to round-trip. Nothing round-trips here.
KIND_PATIENT_NAME: Final = "PATIENT_NAME"
KIND_ADDRESS: Final = "ADDRESS"
KIND_DATE: Final = "DATE"
KIND_PHONE: Final = "PHONE"
KIND_FAX: Final = "FAX"
KIND_EMAIL: Final = "EMAIL"
KIND_SSN: Final = "SSN"
KIND_MRN: Final = "MRN"
KIND_MEMBER_ID: Final = "MEMBER_ID"
KIND_ACCOUNT: Final = "ACCOUNT"
KIND_LICENSE: Final = "LICENSE"
KIND_VEHICLE: Final = "VEHICLE"
KIND_DEVICE: Final = "DEVICE"
KIND_URL: Final = "URL"
KIND_IP: Final = "IP"
KIND_BIOMETRIC: Final = "BIOMETRIC"
KIND_PHOTO: Final = "PHOTO"
KIND_OTHER_ID: Final = "OTHER_ID"
KIND_AGE_OVER_89: Final = "AGE_OVER_89"

_CATEGORY_TO_KIND: Final[dict[PhiCategory, str]] = {
    PhiCategory.NAME: KIND_PATIENT_NAME,
    PhiCategory.GEO: KIND_ADDRESS,
    PhiCategory.DATE: KIND_DATE,
    PhiCategory.PHONE: KIND_PHONE,
    PhiCategory.FAX: KIND_FAX,
    PhiCategory.EMAIL: KIND_EMAIL,
    PhiCategory.SSN: KIND_SSN,
    PhiCategory.MRN: KIND_MRN,
    PhiCategory.HEALTH_PLAN: KIND_MEMBER_ID,
    PhiCategory.ACCOUNT: KIND_ACCOUNT,
    PhiCategory.CERT_LICENSE: KIND_LICENSE,
    PhiCategory.VEHICLE: KIND_VEHICLE,
    PhiCategory.DEVICE: KIND_DEVICE,
    PhiCategory.URL: KIND_URL,
    PhiCategory.IP: KIND_IP,
    PhiCategory.BIOMETRIC: KIND_BIOMETRIC,
    PhiCategory.PHOTO: KIND_PHOTO,
    PhiCategory.OTHER_UNIQUE_ID: KIND_OTHER_ID,
}

#: Safe Harbor requires ages over 89 to be aggregated, not pseudonymised.
AGE_OVER_89_PATTERN: Final = "age.over_89"
GENERALIZED_AGE: Final = "90+"

# Lower rank wins on an identical span. SMCP ranked ``regex`` above its NER
# tiers for the same reason the structural X12 tier outranks regex here: the
# tier that can see the grid is not guessing.
_TIER_RANK: Final[dict[str, int]] = {
    "x12": 0,
    "regex": 1,
    # SMCP also had: presidio=1, jsl=2, classifier=3, scanner_v2=4. None of
    # those tiers exist in this module (see the module docstring).
}


def category_to_kind(category: PhiCategory, pattern_id: str) -> str | None:
    """Return the mask KIND for a finding.

    ``None`` means *do not pseudonymise* — generalize instead. Currently only
    the age>89 case, matching SMCP's ``deid_map.category_to_kind``. Raises
    ``KeyError`` if ``PhiCategory`` ever grows a member without a mapping, so a
    new identifier class fails loud instead of being silently mis-labelled.
    """
    if pattern_id == AGE_OVER_89_PATTERN:
        return None
    kind = _CATEGORY_TO_KIND.get(category)
    if kind is None:
        raise KeyError(f"no KIND mapping for PhiCategory {category!r}")
    return kind


# ---------------------------------------------------------------------------
# Result types — never carry the matched substring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhiFinding:
    """One resolved PHI span.

    Offsets are into the *input* text. ``replacement`` is the mask that was
    substituted, or — when ``strip=False`` — the mask that would have been.
    The original value appears nowhere on this object by design.
    """

    category: str
    kind: str
    start: int
    end: int
    tier: str
    replacement: str


@dataclass(frozen=True)
class PhiScanResult:
    """Outcome of one scan.

    ``verdict``:

    * ``"clean"`` — scan completed, zero findings.
    * ``"masked"`` — scan completed, findings present, every one substituted.
    * ``"needs_human_review"`` — fail closed. Either nothing was scanned
      (oversized input, tier error) or PHI was found and left in place
      (``strip=False``). ``text`` is the untouched input in both cases; this
      module never hands back a partially-masked string as if it were done.
    """

    text: str
    findings: tuple[PhiFinding, ...]
    counts_by_kind: dict[str, int]
    coverage: str
    verdict: str


# ---------------------------------------------------------------------------
# Internal hit type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Hit:
    pattern_id: str
    category: PhiCategory
    start: int
    end: int
    tier: str
    pattern_rank: int = 999


# ---------------------------------------------------------------------------
# Tier 1 — Safe-Harbor regex, ported from smcp/phi/regex_v2.py
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Pattern:
    pattern_id: str
    category: PhiCategory
    regex: re.Pattern[str]
    #: Which capture group is the identifier. Group 1 on labelled patterns so
    #: the label survives and only the value is masked.
    group: int = 0


def _c(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, flags)


_PATTERNS: Final[tuple[_Pattern, ...]] = (
    # (A) Names — regex cannot match free-form names; only "Patient: Jane Doe"
    # style labels. The unlabelled case is the NER gap (see module docstring).
    _Pattern(
        "name.labeled",
        PhiCategory.NAME,
        _c(
            r"\b(?:[Pp]atient|[Nn]ame|PT|[Pp]rovider|[Dd]r|[Pp]hysician)[:\s]+"
            r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\b"
        ),
        group=1,
    ),
    # (B) Geographic subdivisions smaller than a state
    _Pattern(
        "geo.us_street",
        PhiCategory.GEO,
        _c(
            # The name part is 1-4 space-separated words, each with no
            # internal whitespace. The original class included a space and was
            # followed by \s+, giving the engine exponentially many ways to
            # split a long whitespace-heavy run — catastrophic backtracking on
            # input that never matches.
            r"\b\d{1,5}(?:\s+[A-Za-z0-9\.\-]{1,30}){1,4}\s+"
            r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|"
            r"Ln|Lane|Ct|Court|Way|Pkwy|Pl|Place)\b",
            re.IGNORECASE,
        ),
    ),
    _Pattern(
        "geo.us_zip5",
        PhiCategory.GEO,
        _c(r"\b\d{5}(?:-\d{4})?\b"),
    ),
    # (C) Dates (excluding bare years) — MM/DD/YYYY, MM-DD-YYYY, YYYY-MM-DD
    _Pattern(
        "date.mdy",
        PhiCategory.DATE,
        _c(r"\b(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])[/\-](?:19|20)\d{2}\b"),
    ),
    _Pattern(
        "date.ymd",
        PhiCategory.DATE,
        _c(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b"),
    ),
    # SMCP's version spanned the label plus ONE digit, which would mask
    # "DOB: 1" and leave the rest of the date in the clear. Fixed.
    _Pattern(
        "date.dob_label",
        PhiCategory.DATE,
        _c(r"\b(?:DOB|D\.O\.B\.?)[:\s#]*([0-9][0-9/\-\.]{4,9}[0-9])", re.IGNORECASE),
        group=1,
    ),
    # (E) Fax numbers (labeled) — specialization, must precede generic phone
    _Pattern(
        "fax.labeled",
        PhiCategory.FAX,
        _c(
            r"\bfax[:\s#]*(\+?1?[\s\-\.]?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4})\b",
            re.IGNORECASE,
        ),
        group=1,
    ),
    # (D) Phone numbers
    _Pattern(
        "phone.us",
        PhiCategory.PHONE,
        _c(r"(?<!\d)(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}(?!\d)"),
    ),
    # (F) Email
    _Pattern(
        "email.rfc5322_lite",
        PhiCategory.EMAIL,
        _c(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),
    # (G) SSN — also catches ITIN (9xx-xx-xxxx) and invalid-area edge shapes
    # because all three are PHI under Safe-Harbor.
    _Pattern(
        "ssn.dashed",
        PhiCategory.SSN,
        _c(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    _Pattern(
        "ssn.spaced",
        PhiCategory.SSN,
        _c(r"\b\d{3}\s\d{2}\s\d{4}\b"),
    ),
    _Pattern(
        "ssn.labeled9",
        PhiCategory.SSN,
        _c(r"\b(?:SSN|SS#|Social Security)[:\s#]*(\d{9})\b", re.IGNORECASE),
        group=1,
    ),
    # (H) MRN — require at least one digit so stray "record: patient" prose
    # does not flag.
    _Pattern(
        "mrn.labeled",
        PhiCategory.MRN,
        _c(r"\b(?:MRN|Mrn|mrn)[:\s#]*((?=[A-Z0-9\-]{5,}\b)[A-Z0-9\-]*\d[A-Z0-9\-]*)\b"),
        group=1,
    ),
    _Pattern(
        "mrn.chart",
        PhiCategory.MRN,
        _c(
            r"\b(?:[Cc]hart|[Rr]ecord)[:\s#]*"
            r"((?=[A-Z0-9\-]{5,}\b)[A-Z0-9\-]*\d[A-Z0-9\-]*)\b"
        ),
        group=1,
    ),
    # (I) Health plan beneficiary — require at least one digit in identifier
    _Pattern(
        "health_plan.member",
        PhiCategory.HEALTH_PLAN,
        _c(
            r"\b(?:[Mm]ember|[Ss]ubscriber|[Pp]olicy)[:\s#]*"
            r"((?=[A-Z0-9\-]{6,}\b)[A-Z0-9\-]*\d[A-Z0-9\-]*)\b"
        ),
        group=1,
    ),
    _Pattern(
        "health_plan.medicare_mbi",
        PhiCategory.HEALTH_PLAN,
        _c(r"\b[1-9][A-Z][A-Z0-9]\d[A-Z][A-Z0-9]\d[A-Z]{2}\d{2}\b"),
    ),
    # (J) Account numbers
    _Pattern(
        "account.labeled",
        PhiCategory.ACCOUNT,
        _c(r"\b(?:[Aa]cct|[Aa]ccount)[:\s#]*(\d{6,})\b"),
        group=1,
    ),
    # (K) Certificate / license numbers — require at least one digit
    _Pattern(
        "cert_license.labeled",
        PhiCategory.CERT_LICENSE,
        _c(
            r"\b(?:[Ll]icense|[Cc]ert(?:ificate)?|NPI|DEA)[:\s#]*"
            r"((?=[A-Z0-9\-]{6,}\b)[A-Z0-9\-]*\d[A-Z0-9\-]*)\b"
        ),
        group=1,
    ),
    # (L) Vehicle / plate / VIN
    _Pattern(
        "vehicle.vin",
        PhiCategory.VEHICLE,
        _c(r"\b[A-HJ-NPR-Z0-9]{17}\b"),
    ),
    _Pattern(
        "vehicle.plate",
        PhiCategory.VEHICLE,
        _c(
            r"\b(?:[Pp]late|[Tt]ag)[:\s#]*"
            r"((?=[A-Z0-9\-]{5,8}\b)[A-Z0-9\-]*\d[A-Z0-9\-]*)\b"
        ),
        group=1,
    ),
    # (M) Device identifiers / serials
    _Pattern(
        "device.serial",
        PhiCategory.DEVICE,
        _c(
            r"\b(?:[Ss]erial|[Ss]/[Nn]|SN|sn)[:\s#]*"
            r"((?=[A-Z0-9\-]{6,}\b)[A-Z0-9\-]*\d[A-Z0-9\-]*)\b"
        ),
        group=1,
    ),
    # (N) URLs
    _Pattern(
        "url.http",
        PhiCategory.URL,
        _c(r"\bhttps?://[^\s<>\"]{4,}", re.IGNORECASE),
    ),
    # (O) IP addresses
    _Pattern(
        "ip.v4",
        PhiCategory.IP,
        _c(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
    ),
    _Pattern(
        "ip.v6",
        PhiCategory.IP,
        _c(r"\b(?:[A-F0-9]{1,4}:){7}[A-F0-9]{1,4}\b", re.IGNORECASE),
    ),
    # (P) Biometric identifiers — surface when labeled
    _Pattern(
        "biometric.labeled",
        PhiCategory.BIOMETRIC,
        _c(
            r"\b(?:[Ff]ingerprint|[Rr]etina|[Ii]ris|[Vv]oiceprint|DNA)"
            r"[:\s#]*((?=[A-Z0-9\-]+\b)[A-Z0-9\-]*\d[A-Z0-9\-]*)\b"
        ),
        group=1,
    ),
    # (Q) Photo — surface labeled file references
    _Pattern(
        "photo.labeled",
        PhiCategory.PHOTO,
        _c(
            r"\b(?:[Pp]hoto|[Hh]eadshot|[Ff]ace[\- ][Ii]mage)[:\s]+"
            r"([A-Za-z0-9_\-/\.]+\.(?:jpg|jpeg|png|tiff|bmp))\b"
        ),
        group=1,
    ),
    # (R) Other unique identifiers — broad catch-all UUID / GUID
    _Pattern(
        "other.uuid",
        PhiCategory.OTHER_UNIQUE_ID,
        _c(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
    ),
    # Safe-Harbor (C) tail: ages over 89 must be aggregated into a 90+ bucket.
    # SMCP got this from its JSL tier (``jsl.age_over_89``); the obligation is
    # carried here by a regex so the generalization path is not lost with it.
    _Pattern(
        AGE_OVER_89_PATTERN,
        PhiCategory.DATE,
        # The trailing (?!\+) keeps the generalization idempotent: an already
        # bucketed "aged 90+" is not re-matched.
        _c(r"\bage[ds]?\s*(?:is|of|[:=])?\s*(9\d|1[0-4]\d)\b(?!\+)", re.IGNORECASE),
        group=1,
    ),
)

#: Patterns whose span may be a bare 10-digit provider NPI rather than PHI.
#: Suppression is deliberately NOT applied to the labelled patient-identifier
#: patterns — "Account #4455667788" is an account number because the document
#: says so, and SMCP's blanket 10-digit rule would have thrown it away.
_NPI_SUPPRESSIBLE: Final[frozenset[str]] = frozenset(
    {"phone.us", "cert_license.labeled"}
)


def _regex_hits(text: str) -> list[_Hit]:
    """Run every Safe-Harbor pattern. Overlaps are resolved globally later."""
    hits: list[_Hit] = []
    for rank, pat in enumerate(_PATTERNS):
        for match in pat.regex.finditer(text):
            start, end = match.span(pat.group)
            if start < 0 or end <= start:
                continue
            hits.append(
                _Hit(
                    pattern_id=pat.pattern_id,
                    category=pat.category,
                    start=start,
                    end=end,
                    tier="regex",
                    pattern_rank=rank,
                )
            )
    return hits


# ---------------------------------------------------------------------------
# Tier 2 — X12 structural
# ---------------------------------------------------------------------------

# NM1/N1 entity identifier codes that denote a provider, payer, facility or
# other organisation. Everything NOT in this set is treated as patient-side,
# so an unrecognised code fails closed (masked) rather than open (leaked).
_X12_ORG_ENTITIES: Final[frozenset[str]] = frozenset(
    {
        "1P",  # provider
        "2B",  # third-party administrator
        "36",  # employer
        "40",  # receiver
        "41",  # submitter
        "71",  # attending physician
        "72",  # operating physician
        "73",  # other physician
        "77",  # service location
        "80",  # hospital
        "82",  # rendering provider
        "85",  # billing provider
        "87",  # pay-to provider
        "98",  # receiver of service
        "DK",  # ordering provider
        "DN",  # referring provider
        "DQ",  # supervising physician
        "FA",  # facility
        "GB",  # other insured
        "P3",  # primary care provider
        "PE",  # payee
        "PR",  # payer
        "TT",  # transfer to
    }
)

# Segments whose contents are codes, amounts and control numbers — never PHI,
# and destructive to mask. Their elements become suppression zones so no regex
# hit can reach a CPT code, a claim id, a dollar amount or an ISA control
# number. NTE (free-text note) is deliberately absent: notes carry PHI.
_X12_CODE_SEGMENTS: Final[frozenset[str]] = frozenset(
    {
        "AMT",
        "BPR",
        "CAS",
        "CLM",
        "CLP",
        "CUR",
        "GE",
        "GS",
        "HI",
        "HL",
        "IEA",
        "ISA",
        "LX",
        "MIA",
        "MOA",
        "PLB",
        "QTY",
        "SBR",
        "SE",
        "ST",
        "SVC",
        "SV1",
        "SV2",
        "SV3",
    }
)

# Segment ids that close the current NM1/N1 loop.
_X12_LOOP_RESET: Final[frozenset[str]] = frozenset(
    {"ISA", "GS", "GE", "IEA", "ST", "SE", "BHT", "HL", "LX", "CLM", "CLP", "SVC"}
)

# REF qualifiers that carry a patient identifier. 6R (line item control) and
# EI (employer id) are deliberately absent — they are not PHI.
_X12_PHI_REF_QUALIFIERS: Final[dict[str, PhiCategory]] = {
    "SY": PhiCategory.SSN,
    "EA": PhiCategory.MRN,
    "1W": PhiCategory.HEALTH_PLAN,
    "HJ": PhiCategory.HEALTH_PLAN,
    "IG": PhiCategory.HEALTH_PLAN,
    "F6": PhiCategory.HEALTH_PLAN,
    "23": PhiCategory.OTHER_UNIQUE_ID,
}

# NM1/N1 identification-code qualifiers → category of the identifier.
_X12_ID_QUALIFIERS: Final[dict[str, PhiCategory]] = {
    "34": PhiCategory.SSN,
    "SY": PhiCategory.SSN,
    "MI": PhiCategory.HEALTH_PLAN,
    "II": PhiCategory.HEALTH_PLAN,
    "HN": PhiCategory.HEALTH_PLAN,
    "ZZ": PhiCategory.HEALTH_PLAN,
    "MR": PhiCategory.MRN,
    "C": PhiCategory.HEALTH_PLAN,
}

# DTP/DTM qualifiers that are patient-linked dates under Safe Harbor (C).
# Administrative timestamps (405 production date, 009 process date, 050
# received) are excluded — the same distinction SMCP drew in
# ``fp_rules.json`` -> ``date_admin_label_suppress``.
_X12_PATIENT_DATE_QUALIFIERS: Final[frozenset[str]] = frozenset(
    {
        "096",  # discharge
        "232",  # statement from
        "233",  # statement to
        "304",  # latest visit / consultation
        "431",  # onset of current illness
        "434",  # statement period
        "435",  # admission
        "439",  # accident
        "444",  # first visit
        "453",  # acute manifestation
        "454",  # initial treatment
        "455",  # last x-ray
        "471",  # prescription
        "472",  # service
        "484",  # last menstrual period
    }
)

_X12_PER_QUALIFIERS: Final[dict[str, PhiCategory]] = {
    "TE": PhiCategory.PHONE,
    "FX": PhiCategory.FAX,
    "EM": PhiCategory.EMAIL,
}


def _x12_delimiters(text: str) -> tuple[str, str] | None:
    """Return ``(element_separator, segment_terminator)`` or ``None``.

    Read from the ISA envelope at its standard fixed offsets: the element
    separator is ISA[3] and the segment terminator is ISA[105]. Falls back to
    ``~`` when the envelope is malformed but a plausible terminator is present.
    """
    idx = text.find("ISA")
    if idx < 0 or len(text) - idx < 106:
        return None
    elem = text[idx + 3]
    if elem.isalnum() or elem.isspace():
        return None
    term = text[idx + 105]
    if term.isalnum() or term.isspace() or term == elem:
        if "~" not in text:
            return None
        term = "~"
    return elem, term


def _iter_segments(text: str, term: str) -> Iterator[tuple[int, str]]:
    """Yield ``(offset, segment_text)`` pairs, offsets into ``text``."""
    pos = 0
    length = len(text)
    while pos < length:
        end = text.find(term, pos)
        if end < 0:
            if text[pos:].strip():
                yield pos, text[pos:]
            return
        yield pos, text[pos:end]
        pos = end + len(term)


def _elements(segment: str, base: int, elem: str) -> list[tuple[int, int, str]]:
    """Split a segment into ``(start, end, value)`` element triples."""
    out: list[tuple[int, int, str]] = []
    pos = 0
    for part in segment.split(elem):
        out.append((base + pos, base + pos + len(part), part))
        pos += len(part) + len(elem)
    return out


def _x12_hits(text: str) -> tuple[list[_Hit], list[tuple[int, int]]]:
    """Structural pass over X12.

    Returns ``(hits, protected_spans)``. ``protected_spans`` cover elements the
    grid proves are not patient data — a provider's address, a payer's contact
    details, a CPT code, a claim id — so the regex tier cannot mask them.
    """
    delims = _x12_delimiters(text)
    if delims is None:
        return [], []
    elem_sep, term = delims

    hits: list[_Hit] = []
    protected: list[tuple[int, int]] = []
    subject_is_patient = False

    def emit(span: tuple[int, int, str], category: PhiCategory, pid: str) -> None:
        start, end, value = span
        # The structural tier is position-based, so without this check a second
        # pass would happily re-mask its own masks. Keeps scan() idempotent.
        if _MASK_TOKEN_RE.match(value) or _PHI_PLACEHOLDER_RE.match(value):
            return
        if value.strip():
            hits.append(
                _Hit(
                    pattern_id=pid,
                    category=category,
                    start=start,
                    end=end,
                    tier="x12",
                )
            )

    def protect(span: tuple[int, int, str]) -> None:
        start, end, value = span
        if value.strip():
            protected.append((start, end))

    def at(els: list[tuple[int, int, str]], i: int) -> tuple[int, int, str] | None:
        return els[i] if i < len(els) else None

    for offset, segment in _iter_segments(text, term):
        els = _elements(segment, offset, elem_sep)
        sid = els[0][2].strip().upper()
        if not sid:
            continue

        if sid in _X12_LOOP_RESET:
            subject_is_patient = False
        span: tuple[int, int, str] | None
        category: PhiCategory | None

        if sid in _X12_CODE_SEGMENTS:
            for code_span in els[1:]:
                protect(code_span)
            continue

        if sid in ("NM1", "N1"):
            code = (els[1][2].strip().upper()) if len(els) > 1 else ""
            subject_is_patient = code not in _X12_ORG_ENTITIES
            name_idx: tuple[int, ...]
            if sid == "NM1":
                name_idx = (3, 4, 5, 7)
                qual_idx, id_idx = 8, 9
            else:  # N1: N101 code, N102 name, N103 qualifier, N104 id
                name_idx = (2,)
                qual_idx, id_idx = 3, 4
            for i in name_idx:
                span = at(els, i)
                if span is None:
                    continue
                if subject_is_patient:
                    emit(span, PhiCategory.NAME, f"x12.{sid.lower()}_name")
                else:
                    protect(span)
            id_span = at(els, id_idx)
            if id_span is not None:
                qual_span = at(els, qual_idx)
                qual = qual_span[2].strip().upper() if qual_span else ""
                if subject_is_patient:
                    category = _X12_ID_QUALIFIERS.get(
                        qual, PhiCategory.OTHER_UNIQUE_ID
                    )
                    emit(id_span, category, f"x12.{sid.lower()}_id")
                else:
                    protect(id_span)
            continue

        if sid == "N3":
            for i in (1, 2):
                span = at(els, i)
                if span is None:
                    continue
                if subject_is_patient:
                    emit(span, PhiCategory.GEO, "x12.n3_address")
                else:
                    protect(span)
            continue

        if sid == "N4":
            # N401 city, N402 state (Safe Harbor permits state), N403 postal,
            # N406 location/county qualifier value.
            for i in (1, 3, 6):
                span = at(els, i)
                if span is None:
                    continue
                if subject_is_patient:
                    emit(span, PhiCategory.GEO, "x12.n4_geo")
                else:
                    protect(span)
            continue

        if sid == "DMG":
            fmt = els[1][2].strip().upper() if len(els) > 1 else ""
            span = at(els, 2)
            if span is not None and fmt in ("D8", "D9"):
                emit(span, PhiCategory.DATE, "x12.dmg_dob")
            continue

        if sid == "PER":
            for qi in (3, 5, 7):
                qual_span = at(els, qi)
                val_span = at(els, qi + 1)
                if qual_span is None or val_span is None:
                    continue
                category = _X12_PER_QUALIFIERS.get(qual_span[2].strip().upper())
                if category is None:
                    continue
                if subject_is_patient:
                    emit(val_span, category, "x12.per_contact")
                else:
                    protect(val_span)
            continue

        if sid == "REF":
            qual = els[1][2].strip().upper() if len(els) > 1 else ""
            span = at(els, 2)
            category = _X12_PHI_REF_QUALIFIERS.get(qual)
            if span is not None:
                if category is not None:
                    emit(span, category, "x12.ref_id")
                else:
                    protect(span)
            continue

        if sid == "DTP":
            qual = els[1][2].strip() if len(els) > 1 else ""
            span = at(els, 3)
            if span is not None and qual in _X12_PATIENT_DATE_QUALIFIERS:
                emit(span, PhiCategory.DATE, "x12.dtp_date")
            continue

        if sid == "DTM":
            qual = els[1][2].strip() if len(els) > 1 else ""
            span = at(els, 2)
            if span is not None and qual in _X12_PATIENT_DATE_QUALIFIERS:
                emit(span, PhiCategory.DATE, "x12.dtm_date")
            continue

    return hits, protected


# ---------------------------------------------------------------------------
# False-positive suppression — ported from scanner_v2._suppress_false_positives
# ---------------------------------------------------------------------------

_UUID_RE: Final = _c(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_CPT_RE: Final = _c(r"^\d{5}$")
_NPI_RE: Final = _c(r"^\d{10}$")
_PHI_PLACEHOLDER_RE: Final = _c(
    r"^\[(?:PATIENT_NAME|DATE_OF_SERVICE|MRN|DATE_OF_BIRTH|PROVIDER_NAME|"
    r"FACILITY_NAME)\]$"
)
#: This module's own mask tokens, so rescanning masked output is idempotent.
_MASK_TOKEN_RE: Final = _c(r"^\[[A-Z_]+_[0-9a-f]{" + str(_PSEUDONYM_HEX_LEN) + r"}\]$")

#: Lookbehind window for the context probes below.
_CONTEXT_CHARS: Final = 32

# A standalone 5-digit number is a ZIP or a CPT/HCPCS code and nothing in the
# digits distinguishes them. SMCP suppressed every one as a CPT code. Here the
# preceding characters decide, with CPT checked first because the X12 composite
# qualifier "HC:" looks like a state abbreviation followed by a separator.
_CPT_CONTEXT_RE: Final = _c(
    r"(?:\b(?:cpt|hcpcs|proc(?:edure)?[_ ]?code)\b\W{0,4}|"
    r"\b(?:HC|AD|IV|ER|WK|NU|UI)[:|])$",
    re.IGNORECASE,
)
_US_STATES: Final = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    "MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|"
    "WV|WI|WY|PR|VI|GU|AS|MP"
)
# Prose ZIP context only ("Raleigh, NC 27601"). The X12 grid form
# ("N4*RALEIGH*NC*27601") is the structural tier's job, and letting the
# element separator count here would misread "...*PI*60054" as a ZIP.
_ZIP_CONTEXT_RE: Final = _c(
    r"(?:\b(?:zip|postal)(?:\s+code)?\b\W{0,4}|\b(?:" + _US_STATES + r")[ ,]\s*)$"
)
# A bare 10-digit number is a provider NPI unless something says telephone.
_PHONE_CONTEXT_RE: Final = _c(
    r"(?:\b(?:tel|telephone|phone|fax|mobile|cell)\b\W{0,4}|\*(?:TE|FX)\*)$",
    re.IGNORECASE,
)


class _SpanIndex:
    """Sorted span set with an O(log n) containment probe."""

    __slots__ = ("_starts", "_ends")

    def __init__(self, spans: list[tuple[int, int]]) -> None:
        ordered = sorted(spans)
        self._starts: list[int] = [s for s, _ in ordered]
        self._ends: list[int] = [e for _, e in ordered]
        # Prefix-max of ends so a probe covers nested/adjacent spans.
        running = 0
        for i, end in enumerate(self._ends):
            running = max(running, end)
            self._ends[i] = running

    def contains(self, start: int, end: int) -> bool:
        if not self._starts:
            return False
        i = bisect.bisect_right(self._starts, start) - 1
        return i >= 0 and self._ends[i] >= end


def _suppress_false_positives(
    hits: list[_Hit], text: str, protected: _SpanIndex
) -> list[_Hit]:
    """Drop regex hits that are provably not patient identifiers.

    Only the regex tier is filtered. The X12 tier read the segment grid, so its
    findings are authoritative and are never second-guessed here.

    The matched span is tested in-process against the allowlists below and is
    never logged or returned.
    """
    kept: list[_Hit] = []
    for hit in hits:
        if hit.tier != "regex":
            kept.append(hit)
            continue
        if protected.contains(hit.start, hit.end):
            continue
        span_text = text[hit.start : hit.end]
        before = text[max(0, hit.start - _CONTEXT_CHARS) : hit.start]
        # Surrogate claim/row UUIDs are system keys, not personal identifiers.
        if hit.category == PhiCategory.OTHER_UNIQUE_ID and _UUID_RE.match(span_text):
            continue
        # 5-digit: CPT/HCPCS unless the context says ZIP.
        if hit.category == PhiCategory.GEO and _CPT_RE.match(span_text):
            looks_like_zip = bool(_ZIP_CONTEXT_RE.search(before)) and not (
                _CPT_CONTEXT_RE.search(before)
            )
            if not looks_like_zip:
                continue
        # Bare 10-digit: provider NPI unless the context says telephone.
        if (
            hit.pattern_id in _NPI_SUPPRESSIBLE
            and _NPI_RE.match(span_text)
            and not _PHONE_CONTEXT_RE.search(before)
        ):
            continue
        # Redaction markers — masking a mask would loop.
        if _PHI_PLACEHOLDER_RE.match(span_text) or _MASK_TOKEN_RE.match(span_text):
            continue
        kept.append(hit)
    return kept


# ---------------------------------------------------------------------------
# Overlap resolution — ported from deidentify._select_non_overlapping
# ---------------------------------------------------------------------------


def _select_non_overlapping(hits: list[_Hit]) -> list[_Hit]:
    """Keep earliest-start / widest / most-precise spans; drop overlaps.

    Sort key: earliest start, then widest span, then lowest tier rank so the
    structural tier beats a regex guess on an identical span, then declaration
    order so an earlier, more specific pattern beats a later catch-all.
    """
    ordered = sorted(
        hits,
        key=lambda h: (
            h.start,
            -(h.end - h.start),
            _TIER_RANK.get(h.tier, 9),
            h.pattern_rank,
        ),
    )
    chosen: list[_Hit] = []
    last_end = -1
    for hit in ordered:
        if hit.start >= last_end and hit.end > hit.start:
            chosen.append(hit)
            last_end = hit.end
    return chosen


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


class _Masker:
    """Per-run irreversible pseudonym allocator.

    ``salt`` is random per run and thrown away with the object, so the mapping
    from value to pseudonym exists only for the duration of the call.
    """

    __slots__ = ("_salt", "_cache")

    def __init__(self) -> None:
        self._salt: bytes = secrets.token_bytes(32)
        self._cache: dict[tuple[str, str], str] = {}

    def mask(self, kind: str, value: str) -> str:
        key = (kind, value)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        digest = hmac.new(
            self._salt, f"{kind}\x00{value}".encode(), hashlib.sha256
        ).hexdigest()
        token = f"[{kind}_{digest[:_PSEUDONYM_HEX_LEN]}]"
        self._cache[key] = token
        return token

    def burn(self) -> None:
        """Drop the salt and the value→pseudonym cache."""
        self._salt = b""
        self._cache.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _not_scanned(text: str) -> PhiScanResult:
    """Fail-closed result: nothing scanned, nothing masked, nothing claimed."""
    return PhiScanResult(
        text=text,
        findings=(),
        counts_by_kind={},
        coverage=COVERAGE_NOT_SCANNED,
        verdict=VERDICT_NEEDS_HUMAN_REVIEW,
    )


def _detect(text: str) -> list[_Hit]:
    """Run every available tier and resolve overlaps. May raise."""
    x12, protected_spans = _x12_hits(text)
    hits = x12 + _regex_hits(text)
    hits = _suppress_false_positives(hits, text, _SpanIndex(protected_spans))
    return _select_non_overlapping(hits)


def _scan_with(text: str, *, strip: bool, masker: _Masker, base: int) -> PhiScanResult:
    """Scan one string with a caller-supplied masker.

    ``base`` shifts reported offsets, so ``scan_bundle`` can present findings
    against a virtual concatenation of the bundle.
    """
    if not text:
        return PhiScanResult(
            text=text,
            findings=(),
            counts_by_kind={},
            coverage=COVERAGE_REGEX_SAFE_HARBOR,
            verdict=VERDICT_CLEAN,
        )
    if len(text) > MAX_SCAN_CHARS:
        return _not_scanned(text)

    try:
        chosen = _detect(text)
        if len(chosen) > MAX_FINDINGS:
            return _not_scanned(text)

        findings: list[PhiFinding] = []
        out = text
        # Reverse-order splice: a mask differs in length from the span it
        # replaces, so substitute from the highest offset down and every
        # earlier offset stays valid.
        for hit in sorted(chosen, key=lambda h: h.start, reverse=True):
            kind = category_to_kind(hit.category, hit.pattern_id)
            if kind is None:
                kind, replacement = KIND_AGE_OVER_89, GENERALIZED_AGE
            else:
                replacement = masker.mask(kind, text[hit.start : hit.end])
            if strip:
                out = out[: hit.start] + replacement + out[hit.end :]
            findings.append(
                PhiFinding(
                    category=str(hit.category),
                    kind=kind,
                    start=base + hit.start,
                    end=base + hit.end,
                    tier=hit.tier,
                    replacement=replacement,
                )
            )
    except Exception:  # noqa: BLE001 - any tier failure must fail closed
        return _not_scanned(text)

    findings.reverse()  # restore left-to-right document order

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1

    if not findings:
        verdict = VERDICT_CLEAN
    elif strip:
        verdict = VERDICT_MASKED
    else:
        # PHI found and left in place — a human decides what happens next.
        verdict = VERDICT_NEEDS_HUMAN_REVIEW

    return PhiScanResult(
        text=out if strip else text,
        findings=tuple(findings),
        counts_by_kind=counts,
        coverage=COVERAGE_REGEX_SAFE_HARBOR,
        verdict=verdict,
    )


def scan(text: str, *, strip: bool) -> PhiScanResult:
    """Detect PHI in ``text``; mask it irreversibly when ``strip`` is true.

    With ``strip=True`` the returned ``text`` has every detected span replaced
    by a per-run pseudonym and the verdict is ``"masked"``. With ``strip=False``
    the text comes back byte-identical, the findings are still reported, and the
    verdict is ``"needs_human_review"`` because PHI is still in the string.

    Oversized input or a failing tier returns ``"needs_human_review"`` with the
    input unchanged. A partially-masked string is never returned as clean.
    """
    masker = _Masker()
    try:
        return _scan_with(text, strip=strip, masker=masker, base=0)
    finally:
        masker.burn()


def scan_bundle(
    files: dict[str, str], *, strip: bool
) -> tuple[dict[str, str], PhiScanResult]:
    """Scan a whole upload as one unit.

    One salt is shared across every file, so a patient, member id or date of
    service that appears in both the 837 and its 835 gets the same pseudonym in
    both and the agent's cross-file joins still work.

    Returns ``(files, aggregate)``. The masked payload is the first element;
    the aggregate's ``text`` is always empty. Aggregate finding offsets are
    against a virtual concatenation of the files in sorted-name order joined by
    a single newline. If any file fails to scan, the whole bundle is refused:
    the original files come back untouched with ``"needs_human_review"``.
    """
    total = sum(len(v) for v in files.values())
    if total > MAX_SCAN_CHARS:
        return dict(files), _not_scanned("")

    masker = _Masker()
    try:
        out_files: dict[str, str] = {}
        findings: list[PhiFinding] = []
        counts: dict[str, int] = {}
        base = 0
        for name in sorted(files):
            content = files[name]
            result = _scan_with(content, strip=strip, masker=masker, base=base)
            if result.coverage == COVERAGE_NOT_SCANNED:
                return dict(files), _not_scanned("")
            out_files[name] = result.text
            findings.extend(result.findings)
            for kind, count in result.counts_by_kind.items():
                counts[kind] = counts.get(kind, 0) + count
            base += len(content) + 1  # +1 for the virtual newline join
    finally:
        masker.burn()

    if not findings:
        verdict = VERDICT_CLEAN
    elif strip:
        verdict = VERDICT_MASKED
    else:
        verdict = VERDICT_NEEDS_HUMAN_REVIEW

    aggregate = PhiScanResult(
        text="",
        findings=tuple(findings),
        counts_by_kind=counts,
        coverage=COVERAGE_REGEX_SAFE_HARBOR,
        verdict=verdict,
    )
    return out_files, aggregate


__all__ = [
    "AGE_OVER_89_PATTERN",
    "COVERAGE_NOT_SCANNED",
    "COVERAGE_REGEX_SAFE_HARBOR",
    "GENERALIZED_AGE",
    "MAX_FINDINGS",
    "MAX_SCAN_CHARS",
    "PhiCategory",
    "PhiFinding",
    "PhiScanResult",
    "VERDICT_CLEAN",
    "VERDICT_MASKED",
    "VERDICT_NEEDS_HUMAN_REVIEW",
    "category_to_kind",
    "scan",
    "scan_bundle",
]
