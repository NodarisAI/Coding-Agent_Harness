import datetime, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import redact as R  # noqa: E402

NOTE = ("Patient: John Smith DOB 04/12/1961 MRN 00451234 phone (212) 555-0199. Seen 2026-03-02, paid 2026-04-01. "
        "Email jane.doe@gmail.com, SSN 123-45-6789, member id W123456789.")
ISA = "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *260901*1200*^*00501*000000001*0*P*:~"
ERA = (ISA + "GS*HP*S*R*20260901*1200*1*X*005010X221A1~ST*835*0001~CLP*CLAIM778899*1*500*300**MC*PAYERCTL123~"
       "NM1*QC*1*DOE*JANE*Q***MI*W123456789~DMG*D8*19610412~N3*42 OAK ST~SE*5*0001~GE*1*1~IEA*1*000000001~")
REAL = ["John Smith", "00451234", "555-0199", "jane.doe", "123-45-6789", "W123456789", "04/12/1961", "2026-03-02"]


def test_every_identifier_is_replaced_and_none_survives():
    r = R.redact_text(NOTE)
    assert r.verdict == "redacted"
    for value in REAL:
        assert value not in r.text, value
    assert not re.search(r"\[[A-Z_]+_[0-9a-f]+\]", r.text), "surrogates, never tags"


def test_surrogates_keep_kind_and_format():
    r = R.redact_text(NOTE)
    assert re.search(r"DOB \d{2}/\d{2}/\d{4} MRN \d{8} phone \(\d{3}\) 555-01\d{2}\.", r.text)
    assert re.search(r"SSN 9\d{2}-\d{2}-\d{4}", r.text)
    assert re.search(r"Email person\.[0-9a-f]{6}@example\.com", r.text)
    assert re.search(r"member id [A-Z]\d{9}\.", r.text)
    assert re.search(r"Patient: [A-Z][a-z]+ [A-Z][a-z]+ DOB", r.text)


def test_date_intervals_survive_the_shift():
    r = R.redact_text(NOTE)
    seen, paid = re.search(r"Seen (\S+), paid (\S+)\.", r.text).groups()
    d = lambda s: datetime.date.fromisoformat(s)  # noqa: E731
    assert (d(paid) - d(seen)).days == 30
    assert seen != "2026-03-02"


def test_one_value_one_surrogate_within_a_run_and_fresh_ones_across_runs():
    s = R.Surrogates()
    a = R.redact_text("member id W123456789 and again member id W123456789", s).text
    ids = re.findall(r"member id (\S+)", a)
    assert ids[0] == ids[1]
    other = {re.search(r"member id (\S+)", R.redact_text("member id W123456789").text).group(1) for _ in range(5)}
    assert len(other) > 1, "a surrogate is drawn at random, never computed from the value"


def test_x12_structure_is_kept_and_identifiers_are_replaced():
    r = R.redact_text(ERA)
    assert r.text.count("~") == ERA.count("~")
    assert [seg.count("*") for seg in r.text.split("~")] == [seg.count("*") for seg in ERA.split("~")]
    for value in ("CLAIM778899", "PAYERCTL123", "DOE", "JANE", "W123456789", "19610412", "42 OAK ST"):
        assert value not in r.text, value
    nm1 = next(seg for seg in r.text.split("~") if seg.startswith("NM1*QC"))
    assert re.fullmatch(r"NM1\*QC\*1\*[A-Z]+\*[A-Z]+\*[A-Z]\*\*\*MI\*[A-Z]\d{9}", nm1)
    assert r.counts["PATIENT_NAME"] == 3 and r.counts["CLAIM_ID"] == 2


def test_the_mapping_is_never_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    src = tmp_path / "note.txt"
    src.write_text(NOTE)
    before = sorted(os.listdir(tmp_path))
    r = R.redact_file(str(src))
    assert r.verdict == "redacted"
    assert sorted(os.listdir(tmp_path)) == before
    assert "W123456789" not in repr(r.report())


def test_a_document_that_cannot_be_scanned_is_refused(tmp_path):
    pdf = tmp_path / "chart.pdf"
    pdf.write_bytes(b"%PDF-1.7 binary")
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"John Smith\x00\x01\x02")
    for p in (pdf, blob):
        r = R.redact_file(str(p))
        assert r.verdict == "refused" and r.text == ""


def test_developer_prompt_is_not_patient_information():
    r = R.redact_text("fix the bug Bharat found in intake.py on 2026-09-01, see https://github.com/x/y")
    assert r.strong == {}


def test_names_without_a_label_are_caught_near_a_patient_cue_and_colleagues_are_not():
    r = R.redact_text("Why was member id W123456789 for John Smith, DOB 04/12/1961, denied?")
    assert "John Smith" not in r.text and r.counts.get("PATIENT_NAME") == 1
    assert "SMITH, JOHN" not in R.redact_text("Patient SMITH, JOHN Q was seen 03/02/2026").text
    assert R.redact_text("Bharat Kumar fixed the intake view yesterday").counts == {}
    assert "Blue Cross" in R.redact_text("Blue Cross denied member id W123456789").text


def test_redact_check_exits_1_when_it_finds_identifiers():
    import subprocess
    cli = os.path.join(os.path.dirname(__file__), "..", "..", "bin", "nodaris-harness")
    run = lambda text: subprocess.run([cli, "redact", "--check"], input=text, capture_output=True, text=True)
    clean, dirty = run("12 claims ready for review\n"), run("Patient: John Smith DOB 03/14/1961 Member ID W123456789\n")
    assert clean.returncode == 0 and clean.stdout == ""
    assert dirty.returncode == 1 and dirty.stdout == "" and "John" not in dirty.stderr


def test_a_phone_stand_in_never_keeps_the_real_line_number():
    for _ in range(300):
        assert "555-0199" not in R.redact_text("call (212) 555-0199 today").text
