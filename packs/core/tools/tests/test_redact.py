import os, re, subprocess, sys

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "redact.py")
SAMPLE = ("Import failed for patient John Smith DOB 04/23/1971, member id W123456789, claim #88213-A, SSN 123-45-6789.\n"
          "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *260101*1200*^*00501*000000001*0*P*:~"
          "NM1*QC*1*SMITH*JOHN****MI*W123456789~CLP*CLAIM001*1*100*80**12*ABC123~REF*EA*MRN7788~\n")
VALUES = ["John Smith", "04/23/1971", "W123456789", "88213-A", "123-45-6789", "SMITH", "CLAIM001", "ABC123", "MRN7788"]


def run(text, *args):
    return subprocess.run([sys.executable, TOOL, *args], input=text, capture_output=True, text=True, timeout=30)


def test_every_identifier_is_removed():
    p = run(SAMPLE)
    assert p.returncode == 0
    for v in VALUES:
        assert v not in p.stdout, v
        assert v not in p.stderr, v
    assert "CLP*[CLAIM_ID_" in p.stdout and "*1*100*80**12*" in p.stdout  # structure and amounts survive


def test_same_value_same_pseudonym_within_a_run():
    p = run("member id W123456789 and again member id W123456789\n")
    toks = [t for t in p.stdout.split() if t.startswith("[MEMBER_ID_")]
    assert len(toks) == 2 and toks[0] == toks[1]


def test_ordinary_engineering_text_is_untouched():
    text = "Test case 10000 passed; policy v2-beta applied; see claims table for 3 rows.\n"
    assert run(text).stdout == text


def test_check_mode_prints_no_text():
    p = run(SAMPLE, "--check")
    assert p.stdout == "" and "SSN" in p.stderr


def test_text_that_merely_contains_isa_is_not_treated_as_x12():
    text = "VISA payment declined; REF*check that ISA*is fine; NM1 lookup failed for ~ row 4~ ok\n"
    assert run(text).stdout == text


def test_second_interchange_is_also_redacted():
    one = SAMPLE.split("\n")[1]
    p = run(one + "\n" + one.replace("W123456789", "W999888777") + "\n")
    assert "W123456789" not in p.stdout and "W999888777" not in p.stdout and "CLAIM001" not in p.stdout


def test_oversized_input_is_refused_not_passed_through():
    p = run("John Smith DOB 04/23/1971 " * 200000)
    assert p.returncode == 2 and p.stdout == ""


def test_one_identifier_keeps_one_pseudonym_across_log_and_x12():
    isa = "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *260101*1200*^*00501*000000001*0*P*:~"
    text = ("error for member ID: MBR99887766\n" + isa +
            "ST*835*0001~NM1*QC*1*FIXTURE*PERSON****MI*MBR99887766~SE*2*0001~IEA*1*000000001~\n")
    out = run(text).stdout
    assert "MBR99887766" not in out
    tokens = re.findall(r"\[MEMBER_ID_[0-9a-f]{8}\]", out)
    assert len(tokens) == 2 and tokens[0] == tokens[1]
