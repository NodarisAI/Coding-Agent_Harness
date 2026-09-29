"""Copy lint: after an edit to a UI file, name the phrases in its visible text that read as machine-written.

Advice, never a block: the agent gets the phrase, the line and the rule, and rewrites it with the plain-copy skill.
Only text a person would see is checked (JSX text, string literals, HTML text), not code or comments.
"""
import os, re

UI_EXT = {".tsx", ".jsx", ".vue", ".svelte", ".html", ".htm", ".astro", ".mdx"}
UI_JSON = re.compile(r"(^|/)(locales?|i18n|messages|translations?)/[^/]+\.json$")
STRINGS = re.compile(r""">([^<>{}]{3,})<|"([^"\\\n]{6,})"|'([^'\\\n]{6,})'|`([^`\\\n]{6,})`""")

RULES = [
    ("inflated", r"\b(seamless(ly)?|effortless(ly)?|supercharge\w*|game[- ]chang\w*|cutting[- ]edge|next[- ]level|revolutioni[sz]\w*|world[- ]class|best[- ]in[- ]class|unlock(s|ing)? (the|your)|unleash\w*|empower\w*|elevate\w*|harness(es|ing)? the power)\b",
     "an inflated claim; say what the screen does, in the words the user uses"),
    ("empty", r"\b(delve|leverag\w+|robust solution|in today's|it'?s (important|worth) (to note|noting)|please note that|at the end of the day|a (wide|broad) (range|array) of|streamline (your|the))\b",
     "filler; cut it or name the concrete thing"),
    ("chat", r"\b(press (the )?(button|enter|save|submit)|hit (the )?(button|save))\b",
     "product text says click, not press or hit"),
    ("cheer", r"\b(oops|uh[- ]oh|whoops|yay|woohoo|awesome!)",
     "an error or confirmation in professional software states what happened and what to do next"),
    ("dash", r"\s—\s|—",
     "an em dash in interface text; use a full stop or a comma"),
    ("emoji", r"[\U0001F300-\U0001FAFF☀-➿]",
     "an emoji in interface text"),
    ("bang", r"![^\s=]|!\s*$",
     "an exclamation mark; product text states facts calmly"),
]
COMPILED = [(k, re.compile(rx, re.I), why) for k, rx, why in RULES]


def is_ui_file(path):
    path = path or ""
    return os.path.splitext(path)[1].lower() in UI_EXT or bool(UI_JSON.search(path.replace("\\", "/")))


def visible_strings(text):
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "/*", "*", "import ", "export ", "#")) or "className" in line and ">" not in line:
            continue
        for m in STRINGS.finditer(line):
            s = next(g for g in m.groups() if g)
            if re.search(r"[A-Za-z]{3,} [A-Za-z]{2,}", s) and not re.search(r"[\w-]+\.(tsx?|jsx?|css|json|svg|png)\b|^[\w./:-]+$|https?://", s):
                yield n, s.strip()


def lint(text, limit=8):
    found = []
    for n, s in visible_strings(text or ""):
        for key, rx, why in COMPILED:
            m = rx.search(s)
            if m:
                found.append({"line": n, "rule": key, "phrase": m.group(0).strip() or m.group(0), "why": why, "text": s[:80]})
                break
        if len(found) >= limit:
            break
    return found


def advice(path, text):
    if not is_ui_file(path):
        return ""
    found = lint(text)
    if not found:
        return ""
    rows = "\n".join(f"- line {f['line']}: \"{f['phrase']}\" in \"{f['text']}\": {f['why']}" for f in found)
    return (f"Copy check on {os.path.basename(path)}: {len(found)} phrase(s) in visible text read as machine-written or "
            f"break the copy standard. Rewrite them with the plain-copy skill (keep the meaning, name the concrete "
            f"thing):\n{rows}")
