#!/usr/bin/env python3
"""Build Laya's training data from the answers Jev already gave, on this machine only.

The prompt-shaper hook logs each Jev answer with a 16-character SHA-256 prefix of the prompt, never the text. This
script finds the text again by hashing the person's own prompts in the Claude Code transcripts, then:
  - drops private and personal sessions (lifeos-local, the EAD case, job search, careerdesk, Becoming Project);
  - runs every prompt through the harness redactor and drops any it refuses or finds a strong identifier in;
  - masks names and credentials exactly as the live Jev and Laya path does;
  - splits the rows 80/20 by hash, so a prompt always lands in the same split;
  - writes train.jsonl and holdout.jsonl (mode 0600) under <harness home>/laya/data.

It prints counts only. The dataset never leaves the machine and is never committed or shipped.

    python3 scripts/laya_dataset.py [--log PATH] [--projects DIR] [--out DIR]
"""
import argparse, glob, hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "engine"))
from nodaris_harness import jev, policy, redact  # noqa: E402

PRIVATE_PATH = re.compile(r"lifeos-local|lifeos_local|careerdesk|career-ops|ai-job-search|ead-i765", re.I)
PRIVATE_TEXT = re.compile(r"\b(?:EAD|I-765|I765|USCIS|mandamus|OPT\b|visa|job (?:search|application|posting)s?|"
                          r"careerdesk|career-ops|resume|cover letter|recruiter|Becoming Project|lifeos)\b", re.I)
NOT_A_PROMPT = ("<task-notification", "<local-command", "<command-", "<system-reminder", "Caveat:", "<bash-",
                "[Request interrupted", "This session is being continued")
HOLDOUT_EVERY = 5


def phash(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def labels(a):
    """The harness's questions, from one logged Jev answer; None when the core answers are missing."""
    if not isinstance(a, dict):
        return None
    out = {}
    for q in ("type", "effort", "shape_reply"):
        v = a.get(q)
        if isinstance(v, list) and v and isinstance(v[0], str):
            out[q] = v[0]
    for q in ("critical", "reply"):
        v = a.get(q)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[q] = bool(v >= 0.5)
    if out.get("type") not in jev.TYPES or out.get("effort") not in jev.EFFORTS:
        return None
    if "shape_reply" in out and out["shape_reply"] not in jev.SHAPES:
        del out["shape_reply"]
    return out


def answers(log):
    """ph -> (labels, first message in session), from the prompt-shaper log."""
    got, seen_sid = {}, set()
    with open(log) as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            j = r.get("jev") if isinstance(r, dict) else None
            ph = r.get("ph") if isinstance(r, dict) else None
            if not (isinstance(j, dict) and j.get("ok") and isinstance(ph, str)):
                continue
            lab = labels(j.get("a"))
            if lab:
                sid = r.get("sid")
                got[ph] = (lab, sid not in seen_sid)
                seen_sid.add(sid)
    return got


def prompts(projects, wanted):
    """ph -> (text, transcript path) for the person's own prompts whose hash is wanted."""
    found = {}
    for path in glob.glob(os.path.join(projects, "*", "*.jsonl")):
        try:
            fh = open(path, "rb")
        except OSError:
            continue
        with fh:
            for raw in fh:
                if b'"type":"user"' not in raw and b'"type": "user"' not in raw:
                    continue
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                msg = r.get("message") if isinstance(r.get("message"), dict) else {}
                text = msg.get("content")
                if r.get("isSidechain") or r.get("isMeta") or r.get("isCompactSummary") or not isinstance(text, str):
                    continue
                ph = phash(text)
                if ph in wanted and ph not in found:
                    found[ph] = (text, path)
    return found


def build(log, projects, out):
    counts = {"answers": 0, "found": 0, "private": 0, "refused": 0, "train": 0, "holdout": 0}
    got = answers(log)
    counts["answers"] = len(got)
    texts = prompts(projects, set(got))
    counts["found"] = len(texts)
    rows = {"train": [], "holdout": []}
    for ph, (text, path) in sorted(texts.items()):
        if PRIVATE_PATH.search(path) or PRIVATE_PATH.search(text) or PRIVATE_TEXT.search(text):
            counts["private"] += 1
            continue
        r = redact.redact_text(text.strip())
        if r.verdict == "refused" or r.strong:
            counts["refused"] += 1
            continue
        safe, _ = jev.mask_secrets(jev.mask_names(r.text))
        lab, first = got[ph]
        split = "holdout" if int(ph, 16) % HOLDOUT_EVERY == 0 else "train"
        rows[split].append({"id": ph, "state": {"message": safe, "first_message_in_session": first}, "labels": lab})
    os.makedirs(out, mode=0o700, exist_ok=True)
    for split, items in rows.items():
        dest = os.path.join(out, split + ".jsonl")
        with os.fdopen(os.open(dest + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            for item in items:
                fh.write(json.dumps(item) + "\n")
        os.replace(dest + ".tmp", dest)
        counts[split] = len(items)
    dist = {}
    for q in ("type", "effort", "shape_reply"):
        c = {}
        for item in rows["train"] + rows["holdout"]:
            v = item["labels"].get(q)
            if v:
                c[v] = c.get(v, 0) + 1
        dist[q] = c
    counts["labels"] = dist
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", default=os.path.expanduser("~/.claude/prompt-shaper/log.jsonl"))
    ap.add_argument("--projects", default=os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
                                                       "projects"))
    ap.add_argument("--out", default=os.path.join(policy.home(), "laya", "data"))
    a = ap.parse_args(argv)
    counts = build(a.log, a.projects, a.out)
    print(json.dumps(counts, indent=1))
    print("Written to %s (this machine only)." % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
