"""Laya's training data: built from Jev's logged answers on this machine, private sessions and identifiers dropped,
names and credentials masked, a stable 80/20 split, files only the person can read, and counts-only output.
Every string is synthetic."""
import hashlib, http.server, json, os, stat, sys, threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import laya_dataset, laya_eval  # noqa: E402

WORK = ["add paging to the invoice export page number %d with a test" % i for i in range(20)]
PRIVATE = "draft the reply to USCIS about my EAD card delay this week"
SSN = "-".join(("123", "45", "6789"))
PHI = "fix the claim for patient Test Patient One SSN " + SSN + " before friday please"
NAMED = "ask Fixture Person to review the billing export change today please"


def ph(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def jev_row(text, sid="s1", t="build"):
    return {"ph": ph(text), "sid": sid, "jev": {"ok": True, "a": {
        "type": [t, 0.8], "effort": ["medium", 0.7], "shape_reply": ["explain", 0.6], "critical": 0.1, "reply": 0.2}}}


def user(text):
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("NODARIS_HARNESS_HOME", str(tmp_path / "home"))
    log = tmp_path / "log.jsonl"
    texts = WORK + [PRIVATE, PHI, NAMED]
    with open(log, "w") as fh:
        for t in texts:
            fh.write(json.dumps(jev_row(t)) + "\n")
        fh.write(json.dumps({"ph": ph("never answered"), "jev": {"ok": False}}) + "\n")
        fh.write("not json\n")
    proj = tmp_path / "projects"
    (proj / "-repo").mkdir(parents=True)
    (proj / "-Users-x-lifeos-local").mkdir(parents=True)
    with open(proj / "-repo" / "s1.jsonl", "w") as fh:
        for t in WORK + [PRIVATE, PHI, NAMED]:
            fh.write(json.dumps(user(t)) + "\n")
        fh.write(json.dumps(dict(user("a subagent prompt"), isSidechain=True)) + "\n")
    with open(proj / "-Users-x-lifeos-local" / "p.jsonl", "w") as fh:
        fh.write(json.dumps(user("private evening journal entry for the tracker")) + "\n")
    return log, proj, tmp_path / "out"


def test_the_dataset_keeps_work_prompts_and_drops_private_and_identified_ones(world, capsys):
    log, proj, out = world
    assert laya_dataset.main(["--log", str(log), "--projects", str(proj), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    counts = json.loads(printed[:printed.rindex("}") + 1])
    assert counts["answers"] == 23 and counts["found"] == 23 and counts["private"] == 1 and counts["refused"] == 1
    assert counts["train"] + counts["holdout"] == 21
    for t in WORK + [PRIVATE, PHI]:
        assert t not in printed
    rows = [json.loads(l) for f in ("train", "holdout") for l in open(out / (f + ".jsonl"))]
    blob = json.dumps(rows)
    assert PRIVATE not in blob and SSN not in blob and "Fixture Person" not in blob
    assert all(r["labels"]["type"] == "build" and r["labels"]["critical"] is False for r in rows)
    for f in ("train", "holdout"):
        assert stat.S_IMODE(os.stat(out / (f + ".jsonl")).st_mode) == 0o600


def test_the_split_is_stable_across_runs(world):
    log, proj, out = world
    laya_dataset.main(["--log", str(log), "--projects", str(proj), "--out", str(out)])
    first = open(out / "holdout.jsonl").read()
    laya_dataset.main(["--log", str(log), "--projects", str(proj), "--out", str(out)])
    assert open(out / "holdout.jsonl").read() == first


def test_evaluation_reports_agreement_and_the_baseline_and_refuses_a_remote_address(world, capsys):
    rows = [{"state": {"message": "m", "first_message_in_session": True},
             "labels": {"type": "build", "effort": "medium", "shape_reply": "explain", "critical": False}}] * 3 + \
           [{"state": {"message": "m", "first_message_in_session": True},
             "labels": {"type": "fix", "effort": "low", "shape_reply": "brief", "critical": True}}]
    preds = [{"type": "build", "effort": "medium", "shape_reply": "explain", "critical": False}] * 4
    report, gate = laya_eval.score(rows, preds)
    assert report["type"] == {"n": 4, "agreement": 0.75, "baseline": 0.75} and gate is False
    assert laya_eval.main(["--url", "http://10.1.2.3:8000/v1/systemone"]) == 2
