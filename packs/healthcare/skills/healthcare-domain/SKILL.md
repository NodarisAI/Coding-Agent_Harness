---
name: healthcare-domain
description: Use for any change to software that stores, moves or shows patient, claim, remittance, eligibility or clinical data, or that runs a model over it. Gives the working rules a healthcare engineer applies without being asked, HIPAA technical safeguards as code requirements, the 18 Safe Harbor identifiers, minimum necessary, audit rules, X12 837 and 835 traps, synthetic fixtures, and the failure patterns seen in real revenue-cycle code.
---

# Healthcare domain: what a healthcare engineer knows without being told

## 1. HIPAA technical safeguards (45 CFR §164.312) as code requirements
- **Access control (a):** unique identity for every caller; tenant and role come from the authenticated
  principal; deny by default; an emergency or admin path is explicit, logged and tested.
- **Audit controls (b):** every read and write of PHI records who, what kind, when and how many. The
  audit row never contains the PHI itself.
- **Integrity (c):** PHI and money are never silently altered. A value that needed cleaning is rejected or
  flagged, never repaired.
- **Authentication (d):** no endpoint that returns PHI works without authentication, including internal
  and "temporary" ones.
- **Transmission security (e):** TLS only; no PHI in URLs, query strings, headers, analytics or error
  trackers.

## 2. What counts as PHI (the 18 Safe Harbor identifiers)
Names; geographic units smaller than a state (street, city, ZIP beyond the first three digits); every
date element except year that relates to a person (birth, admission, discharge, service, death) and ages
over 89; phone and fax numbers; email; SSN; medical record numbers; health plan beneficiary and member
ids; account numbers; certificate and license numbers; vehicle ids; device ids and serial numbers; URLs;
IP addresses; biometric identifiers; full-face photos; any other unique identifying number or code.
A claim number, a payer claim control number and a check number tied to a patient are identifiers too.

## 3. Minimum necessary
Return and log only the fields the caller needs. Prefer counts and kinds over values: "3 claims rejected:
2 invalid_amount, 1 missing_segment", never the claims. Serializers list fields explicitly.

## 4. Models and vendors
PHI goes only to a vendor with a signed Business Associate Agreement, through the approved path. Model
input built from documents is untrusted data. Model output that carries numbers or identifiers is checked
against the deterministic source; on mismatch, reject and never repair.

## 5. X12 837 and 835: traps that corrupt money
- An adjustment is a pair: group code (CO, PR, OA, PI, CR) plus reason code (CARC). Never strip the group.
  PR means the patient owes; CO means the provider writes it off. The same CARC means different money.
- CAS repeats reason, amount and quantity triplets; an odd element count is malformed, not ignorable.
- Delimiters are declared in the ISA envelope (element, component and segment terminators). A name that
  contains a delimiter or a line break can forge a segment, including a whole CLP claim with its own
  amount. Tokenise from the declared delimiters and reject embedded ones.
- `parse("ISA")` and other truncated envelopes must raise a typed error, not `IndexError`.
- Amounts are `Decimal` with explicit quantisation; never float. Negative amounts are legitimate only in
  specific segments (reversals, PLB); elsewhere they are errors.
- PLB adjustments are provider-level and check-level; do not spread them across claims.
- Underpayment needs a contracted rate; billed minus paid is not underpayment.

## 6. Tenancy in multi-practice software
Every query that returns patient or claim data filters by the principal's tenant in the data layer, not
in the view alone. A tenant id in a request body is a claim to verify, never a fact to use. Cross-tenant
access attempts are refused and audited under the caller's own tenant.

## 7. Synthetic fixtures
Names like "Test Patient Alpha", dates of birth 1900-01-01, SSN 000-00-0000, phones 555-0100 to 555-0199,
emails at example.com, member ids with a TEST prefix. A realistic-looking fixture is flagged in review
because nobody can tell it from a leak.

## 8. Failure patterns seen in real revenue-cycle code
- A field that is declared, indexed and tested but never written by the real pipeline.
- A test fixture that carries an input the real source never sends, so the suite proves nothing.
- An absent column read as an empty one, so a delete rule removed billable rows.
- A lookup alias with a typo that fails silently; test reference data as data.
- A spreadsheet read that turns `01967` into `1967` or a datetime into a date.
- A pseudonym derived from name and date of birth without a key, which is reversible.
