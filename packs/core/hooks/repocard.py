"""Detects how a repository is verified, so nobody has to know its commands."""
import json, os, re, subprocess


def root(cwd):
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return cwd


def detect(cwd):
    r = root(cwd or os.getcwd())
    has = lambda *p: os.path.exists(os.path.join(r, *p))
    card = {"root": r, "stack": [], "verify": [], "tests": [], "guides": []}
    for g in ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md", "DECISIONS.md", ".planning/STATE.md"):
        if has(g):
            card["guides"].append(g)
    for v in ("scripts/verify.sh", "verify.sh"):
        if has(v):
            card["verify"].append("bash " + v)
    if has("Makefile"):
        try:
            targets = re.findall(r"^([A-Za-z][\w-]*):", open(os.path.join(r, "Makefile")).read(), re.M)
            card["verify"] += ["make " + t for t in ("verify", "check", "test", "lint") if t in targets]
        except Exception:
            pass
    py = next((p for p in (".venv/bin/python", "venv/bin/python") if has(p)), None)
    if any(has(f) for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "manage.py", "pytest.ini")):
        card["stack"].append("python" + (" (django)" if has("manage.py") else ""))
        runner = (py + " -m pytest") if py else "python3 -m pytest"
        if has("pytest.ini") or has("conftest.py") or has("tests") or _mentions(r, "pyproject.toml", "pytest"):
            card["verify"].append(runner + " -q")
        elif has("manage.py"):
            card["verify"].append((py or "python3") + " manage.py test")
        for tool in ("ruff", "mypy"):
            if _mentions(r, "pyproject.toml", tool) or has(".venv/bin/" + tool):
                card["verify"].append(((".venv/bin/" + tool) if has(".venv/bin/" + tool) else tool) + (" check ." if tool == "ruff" else " ."))
    if has("package.json"):
        try:
            scripts = json.load(open(os.path.join(r, "package.json"))).get("scripts", {})
        except Exception:
            scripts = {}
        pm = "pnpm" if has("pnpm-lock.yaml") else "yarn" if has("yarn.lock") else "npm"
        card["stack"].append("node (" + pm + ")")
        for s in ("verify", "typecheck", "lint", "test", "build"):
            if s in scripts:
                card["verify"].append(f"{pm} run {s}" + (" -- --run" if s == "test" and "vitest" in scripts[s] and "run" not in scripts[s] else ""))
        if not any("tsc" in v or "typecheck" in v for v in card["verify"]) and has("tsconfig.json"):
            card["verify"].append("npx tsc --noEmit")
    for t in ("tests", "test", "src/__tests__", "__tests__", "spec"):
        if has(t):
            card["tests"].append(t + "/")
    seen, uniq = set(), []
    for v in card["verify"]:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    card["verify"] = uniq
    return card


def _mentions(r, name, word):
    try:
        return word in open(os.path.join(r, name)).read()
    except Exception:
        return False


def render(card):
    lines = [f"Repository: {card['root']} ({', '.join(card['stack']) or 'stack not detected'})."]
    if card["verify"]:
        lines.append("Verify with: " + "; ".join(f"`{v}`" for v in card["verify"]) + ".")
    else:
        lines.append("No verify command was detected: find how this repo runs its tests before changing code.")
    if card["tests"]:
        lines.append("Tests live in: " + ", ".join(card["tests"]) + ".")
    if card["guides"]:
        lines.append("Read first: " + ", ".join(card["guides"]) + ".")
    return " ".join(lines)
