"""Reversible delete: move files into the harness trash instead of removing them.

Each call makes one entry, `<home>/trash/<id>/`, holding the moved items and a manifest of where they came from.
`restore` puts an entry back; nothing is ever overwritten on the way back. Emptying the trash is the only permanent
step, and it only removes entries older than the number of days given.
"""
import json, os, secrets, shutil, time

from . import policy

REFUSE = {"/", os.path.expanduser("~")}


def _root():
    d = os.path.join(policy.home(), "trash")
    os.makedirs(d, exist_ok=True)
    return d


def _size(path):
    if os.path.isfile(path) or os.path.islink(path):
        return os.path.getsize(path) if os.path.isfile(path) else 0
    total = 0
    for base, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def _protected(full):
    engine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in (policy.home(), engine):
        root = os.path.realpath(root)
        try:
            if os.path.commonpath([os.path.realpath(full), root]) == root:
                return True
        except ValueError:
            continue
    return full in REFUSE or os.path.realpath(full) in {os.path.realpath(r) for r in REFUSE}


def move(paths, cwd="."):
    """Move paths into one trash entry. Returns (entry id or None, message). Checks everything before moving
    anything, records each move as it happens, and puts the moved items back if a later one fails."""
    targets = []
    for p in paths:
        full = os.path.abspath(os.path.join(cwd, os.path.expanduser(p)))
        if not os.path.lexists(full):
            return None, f"Nothing was moved: {p} does not exist."
        if _protected(full):
            return None, f"Nothing was moved: {p} is the home folder, the root or part of the harness itself."
        if not os.access(os.path.dirname(full), os.W_OK):
            return None, f"Nothing was moved: {p} is in a folder you cannot change."
        targets.append(full)
    if not targets:
        return None, "Name at least one file or folder."
    entry = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    dest = os.path.join(_root(), entry)
    os.makedirs(os.path.join(dest, "items"))
    manifest = {"id": entry, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": []}

    def save():
        with open(os.path.join(dest, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
    try:
        for n, full in enumerate(targets):
            stored = f"{n}-{os.path.basename(full.rstrip('/')) or 'item'}"
            size = _size(full)
            shutil.move(full, os.path.join(dest, "items", stored))
            manifest["items"].append({"from": full, "stored": stored,
                                      "dir": os.path.isdir(os.path.join(dest, "items", stored)), "bytes": size})
            save()
    except OSError as exc:
        for item in reversed(manifest["items"]):
            shutil.move(os.path.join(dest, "items", item["stored"]), item["from"])
        shutil.rmtree(dest, ignore_errors=True)
        return None, f"Nothing was moved: {type(exc).__name__} while moving; everything was put back."
    return entry, (f"Moved {len(manifest['items'])} item(s) to the harness trash as {entry}. Restore them with "
                   f"`nodaris-harness trash --restore {entry}`.")


def entries():
    out = []
    for name in sorted(os.listdir(_root()), reverse=True):
        try:
            with open(os.path.join(_root(), name, "manifest.json")) as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    return out


def restore(entry):
    dest = os.path.join(_root(), os.path.basename(entry))
    try:
        with open(os.path.join(dest, "manifest.json")) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return False, f"There is no trash entry called {entry}."
    clash = [i["from"] for i in manifest["items"] if os.path.lexists(i["from"])]
    if clash:
        return False, "Nothing was restored, because these paths exist again: " + ", ".join(clash[:5])
    for i in manifest["items"]:
        os.makedirs(os.path.dirname(i["from"]), exist_ok=True)
        shutil.move(os.path.join(dest, "items", i["stored"]), i["from"])
    shutil.rmtree(dest)
    return True, f"Restored {len(manifest['items'])} item(s) from {entry}."


def empty(older_than_days):
    cutoff = time.time() - older_than_days * 86400
    gone = 0
    for name in os.listdir(_root()):
        path = os.path.join(_root(), name)
        if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
            shutil.rmtree(path)
            gone += 1
    return gone
