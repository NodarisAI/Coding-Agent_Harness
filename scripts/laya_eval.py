#!/usr/bin/env python3
"""Measure how often Laya agrees with Jev on the held-out prompts, question by question.

Sends each held-out row to the Laya server on this machine (loopback only, the same client the harness uses) and
compares its answers with Jev's. Prints, for each question, the agreement, the majority-class baseline (always
guessing the most common answer) and the number of rows. The gate from the plan: ship Laya to teammates only when
type, effort and reply shape each clearly beat the baseline, with a target of at least 0.75 agreement.

    python3 scripts/laya_eval.py [--data DIR] [--url http://127.0.0.1:8000/v1/systemone] [--limit N]
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "engine"))
from nodaris_harness import jev, policy  # noqa: E402

QUESTIONS = ("type", "effort", "shape_reply", "critical", "reply")
TARGET = 0.75


def load(path, limit=None):
    rows = []
    with open(path) as fh:
        for ln in fh:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
            if limit and len(rows) >= limit:
                break
    return rows


def predict(url, row):
    qs = jev.questions(row["state"].get("first_message_in_session", True))
    try:
        data = jev._post(url, {"state": row["state"], "questions": qs}, None, 10.0)
        ans = jev.parse(data, qs)
    except Exception:  # noqa: BLE001  one failed row is counted as a miss, never a crash
        return {}
    out = {}
    for q, v in ans.items():
        out[q] = v[0] if isinstance(v, tuple) else bool(v >= 0.5)
    return out


def score(rows, preds):
    report = {}
    for q in QUESTIONS:
        pairs = [(r["labels"][q], p.get(q)) for r, p in zip(rows, preds) if q in r["labels"]]
        if not pairs:
            continue
        truth = [t for t, _ in pairs]
        majority = max(set(truth), key=truth.count)
        report[q] = {"n": len(pairs), "agreement": round(sum(1 for t, p in pairs if t == p) / len(pairs), 3),
                     "baseline": round(truth.count(majority) / len(pairs), 3)}
    gate = all(q in report and report[q]["agreement"] >= TARGET and report[q]["agreement"] > report[q]["baseline"]
               for q in ("type", "effort", "shape_reply"))
    return report, gate


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default=os.path.join(policy.home(), "laya", "data"))
    ap.add_argument("--url")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    url = jev.laya_endpoint(a.url)
    if not url:
        print("Laya's address must be on this machine (127.0.0.1, localhost or ::1, with a port).")
        return 2
    rows = load(os.path.join(a.data, "holdout.jsonl"), a.limit)
    if not rows:
        print("No held-out rows. Run scripts/laya_dataset.py first.")
        return 2
    report, gate = score(rows, [predict(url, r) for r in rows])
    print(json.dumps(report, indent=1))
    print("Gate %s: type, effort and reply shape need at least %.2f agreement and must beat the baseline."
          % ("passed" if gate else "not passed", TARGET))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
