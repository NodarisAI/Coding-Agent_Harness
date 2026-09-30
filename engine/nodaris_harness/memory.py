"""Portable lesson memory: mistakes are written down once and recalled when the same situation comes back.

Three libraries, all JSON Lines:
- the repository's, committed with it: <repo>/.nodaris-harness/lessons.jsonl
- the person's own: <harness home>/lessons/lessons.jsonl
- the team's, read-only, refreshed from the memory vault by team sync: <harness home>/lessons/team.jsonl

A lesson has a trigger (keywords, file globs) and an instruction (when, do, don't, why). Recall runs on every prompt
(word overlap with the request, rarer words weighted higher; see match()) and on every file edit (glob match), and each lesson is shown at most once per
session. Lessons never hold patient information: text is redacted before it is stored.
"""
import fnmatch, hashlib, json, math, os, re, time

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


def team_library():
    """Read-only: the team's lessons from the memory vault, refreshed by team sync. Never written by `add`."""
    return os.path.join(home(), "lessons", "team.jsonl")


def load(cwd):
    lessons = []
    seen = set()
    for path in libraries(cwd) + [team_library()]:
        if os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    key = item.get("id") or json.dumps(item, sort_keys=True) if isinstance(item, dict) else None
                    if key and key not in seen:
                        seen.add(key)
                        lessons.append(item)
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


def _format(lessons, lead, matched=None):
    lines = [lead]
    for item in lessons:
        line = f"- When {item['when']}: {item['do']}"
        if item.get("dont"):
            line += f" Don't: {item['dont']}"
        if item.get("why"):
            line += f" Why: {item['why']}"
        if matched and matched.get(item["id"]):
            line += f" (matched: {', '.join(matched[item['id']][:4])})"
        lines.append(line)
    return "\n".join(lines)


def _raw_words(text):
    """Every word, short ones and numbers included, for matching a lesson's own keywords (835, x12, cpt)."""
    return set(re.findall(r"[a-z0-9][a-z0-9_.+-]*", (text or "").lower()))


def match(prompt, lessons):
    """[(score, lesson, matched words)] for the lessons that fit the request, best first.

    A lesson fits when at least two of its words appear in the request and at least one of them is one of its
    keywords or describes its situation (the "when"), so a lesson is never pulled in by its instruction text alone.
    Words that appear in many lessons count for less (inverse document frequency), and when the library is large a
    lesson matched only by such common words is left out."""
    words, raw = _tokens(prompt), _raw_words(prompt)
    docs = []
    for item in lessons:
        when = _tokens(item.get("when", ""))
        keys = {str(k).lower() for k in item.get("keywords", []) if k}
        docs.append((item, when, keys, when | _tokens(item.get("do", "")) | keys))
    n = len(docs) or 1
    df = {}
    for _, _, _, terms in docs:
        for w in terms:
            df[w] = df.get(w, 0) + 1
    idf = lambda w: math.log(1 + n / (1.0 + df.get(w, 0)))  # noqa: E731
    common = max(2, n // 5)
    out = []
    for item, when, keys, terms in docs:
        hit = (words & terms) | (raw & keys)
        if len(hit) < 2 or not (hit & (keys | when)):
            continue
        if n >= 10 and all(df.get(w, 0) > common for w in hit):
            continue
        score = sum(idf(w) for w in hit) + sum(idf(w) for w in hit & keys)
        out.append((score, item, sorted(hit, key=lambda w: -idf(w))))
    out.sort(key=lambda x: -x[0])
    return out


def recall_for_prompt(session_id, cwd, prompt, k=3):
    found = match(prompt, load(cwd))[:k]
    fresh = set(_mark(session_id, [item["id"] for _, item, _ in found]))
    picked = [(item, hit) for _, item, hit in found if item["id"] in fresh]
    if not picked:
        return ""
    return _format([item for item, _ in picked], "Lessons recorded from earlier work that match this request:",
                   {item["id"]: hit for item, hit in picked})


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
