"""The learned profile: what the harness has learned about how this person works, and the only thing the learner writes.

Every change passes one mechanical check against rules/learner-policy.json before it is written: the field must be
on the automatic list and must not name anything on the never list. Changes are additive, logged with their evidence
in history.jsonl, and reversible by id. The learner cannot change guards, the policy, gates or engine code, with or
without approval; those change only when a person edits them.
"""
import hashlib, json, os, re, time

from . import policy

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "rules")


def learner_policy():
    with open(os.path.join(RULES_DIR, "learner-policy.json")) as f:
        return json.load(f)


def _dir():
    d = os.path.join(policy.home(), "profile")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _prefs_path():
    return os.path.join(_dir(), "preferences.json")


def load():
    try:
        return json.load(open(_prefs_path()))
    except (OSError, ValueError):
        return {}


def _save(prefs):
    tmp = _prefs_path() + ".tmp"
    json.dump(prefs, open(tmp, "w"), indent=1, sort_keys=True)
    os.replace(tmp, _prefs_path())


def _log(entry):
    with open(os.path.join(_dir(), "history.jsonl"), "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def history():
    try:
        return [json.loads(line) for line in open(os.path.join(_dir(), "history.jsonl")) if line.strip()]
    except OSError:
        return []


def check_field(field):
    pol = learner_policy()
    key = field.split(":")[0].lower()
    segments = set(re.split(r"[./_-]", key))
    for bad in (b.lower() for b in pol["never"]):
        # Paths match by prefix; words match whole name segments (so "investigate" is not "gate").
        hit = key.startswith(bad) if ("/" in bad or "." in bad) else any(seg.rstrip("s") == bad.rstrip("s") for seg in segments)
        if hit:
            raise PermissionError(f"the learner may never change {field}")
    if field.split(":")[0] not in pol["auto_fields"]:
        raise PermissionError(f"{field} is not on the learner's automatic list")


def apply_change(field, value, evidence, why, by="auto"):
    """Set or extend one profile field. router_keywords:<playbook> and lessons are additive only."""
    check_field(field)
    prefs = load()
    before = json.loads(json.dumps(prefs))
    if field.startswith("router_keywords:"):
        playbook = field.split(":", 1)[1]
        kw = prefs.setdefault("router_keywords", {}).setdefault(playbook, [])
        new = [v for v in value if v not in kw]
        if not new:
            return None
        kw.extend(new)
        value = new
    elif field == "lessons":
        ids = prefs.setdefault("lessons", [])
        if value in ids:
            return None
        ids.append(value)
    else:
        if prefs.get(field) == value:
            return None
        prefs[field] = value
    change_id = hashlib.sha256(f"{field}|{value}|{time.time()}".encode()).hexdigest()[:10]
    _save(prefs)
    _log({"id": change_id, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "field": field, "value": value, "why": why,
          "evidence": evidence, "by": by, "before": before, "shown": False})
    return change_id


def revert(change_id):
    for entry in reversed(history()):
        if entry["id"] == change_id and entry.get("reverted") is None:
            _save(entry["before"])
            _log({"id": change_id, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "reverted": True, "field": entry["field"]})
            return True
    return False


def unshown_changes():
    seen = {e["id"] for e in history() if e.get("reverted") or e.get("shown_at")}
    return [e for e in history() if "value" in e and e["id"] not in seen]


def mark_shown(ids):
    for i in ids:
        _log({"id": i, "shown_at": time.strftime("%Y-%m-%dT%H:%M:%S")})


def summary():
    """The text the person's agent sees at session start: learned preferences and anything new since last time."""
    prefs = load()
    lines = []
    if prefs.get("style.brevity"):
        lines.append({"brief": "This person prefers short answers: lead with the result, a few plain sentences, no recap.",
                      "detailed": "This person asks for more explanation: say what it is, why it matters and what changes for them."}
                     .get(prefs["style.brevity"], ""))
    for pb, words in sorted((prefs.get("router_keywords") or {}).items()):
        lines.append(f"When this person says {', '.join(repr(w) for w in words[:6])}, they mean {pb} work.")
    new = unshown_changes()
    if new:
        lines.append("Learned since the last session (tell the person in one line; `nodaris-harness learn revert ID` undoes one): "
                     + "; ".join(f"{e['id']}: {e['why']}" for e in new[:5]))
        mark_shown([e["id"] for e in new])
    lines = [x for x in lines if x]
    return ("Learned from this person's use of the harness:\n- " + "\n- ".join(lines)) if lines else ""
