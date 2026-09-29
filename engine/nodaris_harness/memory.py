"""Portable lesson memory: mistakes are written down once and recalled when the same situation comes back.

Two libraries, both JSON Lines:
- the team's, committed with the repository: <repo>/.nodaris-harness/lessons.jsonl
- the person's own: <harness home>/lessons/lessons.jsonl

A lesson has a trigger (keywords, file globs) and an instruction (when, do, don't, why). Recall runs on every prompt
(keyword overlap with the request) and on every file edit (glob match), and each lesson is shown at most once per
session. Lessons never hold patient information: text is redacted before it is stored.
"""
import fnmatch, hashlib, json, os, re, time

from . import redact
from .policy import home

STOP = set("""the and for with that this from into have when what then than your you are was were will would
should could about after before also just only over under more most make made does done need needs file files code
please thanks there their them they which while where each every some such like want wants using used use run""".split())


def _tokens(text):
    return {w for w in re.findall(r"[a-z][a-z0-9_]{3,}", (text or "").lower()) if w not in STOP}


def git_root(path):
    d = os.path.abspath(path or ".")
    while d and d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return None


def libraries(cwd):
    out = [os.path.join(home(), "lessons", "lessons.jsonl")]
    root = git_root(cwd)
    if root:
        out.insert(0, os.path.join(root, ".nodaris-harness", "lessons.jsonl"))
    return out


def load(cwd):
    lessons = []
    for path in libraries(cwd):
        if os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    try:
                        lessons.append(json.loads(line))
                    except ValueError:
                        continue
    return lessons


def add(cwd, when, do, why="", dont="", keywords=(), files=(), scope="repo", verified=False):
    sur = redact.Surrogates()
    clean = lambda t: redact.redact_text(t or "", sur).text  # noqa: E731
    lesson = {"id": hashlib.sha256(f"{when}|{do}".encode()).hexdigest()[:10], "when": clean(when), "do": clean(do),
              "dont": clean(dont), "why": clean(why), "keywords": [k.lower() for k in keywords], "files": list(files),
              "created": time.strftime("%Y-%m-%d"), "verified": bool(verified)}
    sur.burn()
    paths = libraries(cwd)
    path = paths[0] if scope == "repo" and len(paths) == 2 else paths[-1]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = {item.get("id") for item in load(cwd)}
    if lesson["id"] not in existing:
        with open(path, "a") as fh:
            fh.write(json.dumps(lesson) + "\n")
    return lesson, path


def _shown_path(session_id):
    d = os.path.join(home(), "state")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, hashlib.sha256(str(session_id).encode()).hexdigest()[:24] + "-lessons.json")


def _mark(session_id, ids):
    path = _shown_path(session_id)
    shown = set(json.load(open(path))) if os.path.exists(path) else set()
    fresh = [i for i in ids if i not in shown]
    with open(path, "w") as fh:
        json.dump(sorted(shown | set(fresh)), fh)
    return fresh


def _format(lessons, lead):
    lines = [lead]
    for item in lessons:
        line = f"- When {item['when']}: {item['do']}"
        if item.get("dont"):
            line += f" Don't: {item['dont']}"
        if item.get("why"):
            line += f" Why: {item['why']}"
        lines.append(line)
    return "\n".join(lines)


def recall_for_prompt(session_id, cwd, prompt, k=3):
    words = _tokens(prompt)
    scored = []
    for item in load(cwd):
        score = len(words & (_tokens(" ".join([item.get("when", ""), item.get("do", "")])) | set(item.get("keywords", []))))
        score += 2 * len(words & set(item.get("keywords", [])))
        if score >= 3:
            scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    picked = [item for _, item in scored[:k]]
    fresh = set(_mark(session_id, [item["id"] for item in picked]))
    picked = [item for item in picked if item["id"] in fresh]
    return _format(picked, "Lessons recorded from earlier work that match this request:") if picked else ""


def recall_for_file(session_id, cwd, path):
    if not path:
        return ""
    root = git_root(cwd) or cwd
    rel = os.path.relpath(os.path.abspath(os.path.join(cwd, path)), root)
    hits = [item for item in load(cwd) if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(os.path.basename(rel), g)
                                              for g in item.get("files", []))]
    fresh = set(_mark(session_id, [item["id"] for item in hits]))
    hits = [item for item in hits if item["id"] in fresh]
    return _format(hits, f"Lessons recorded for {rel}:") if hits else ""


def export(cwd, out_path):
    lessons = load(cwd)
    with open(out_path, "w") as fh:
        for item in lessons:
            fh.write(json.dumps(item) + "\n")
    return len(lessons)
