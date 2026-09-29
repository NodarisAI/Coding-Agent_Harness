"""Design lint: after an edit to a style or UI file, name the visual habits that make an interface look machine-made.

Advice, never a block, like copylint. Rules are regular expressions over CSS, Tailwind classes and inline styles.
A line can opt out with `design-lint-ignore: <reason>`; a marker without a reason does not count.
Idea from the deterministic rules in pbakaus/impeccable (Apache-2.0), rewritten for dense business software.
"""
import os, re

STYLE_EXT = {".css", ".scss", ".sass", ".less", ".tsx", ".jsx", ".vue", ".svelte", ".html", ".htm", ".astro"}
IGNORE = re.compile(r"design-lint-ignore:\s*[A-Za-z]{3,}(\s+[A-Za-z]+)+")

RULES = [
    ("gradient-text", r"bg-clip-text|background-clip\s*:\s*text|-webkit-background-clip\s*:\s*text",
     "gradient-filled text; use one solid colour from the design system so headings stay readable"),
    ("ai-palette", r"from-(purple|violet|fuchsia|indigo)-\d{3}[^\"'`]*to-(blue|cyan|pink|sky)-\d{3}|"
                   r"from-(blue|cyan)-\d{3}[^\"'`]*to-(purple|violet|fuchsia)-\d{3}|"
                   r"linear-gradient\([^)]*#(8b5cf6|7c3aed|a855f7|6366f1)[^)]*#(3b82f6|06b6d4|ec4899)",
     "the purple-to-blue gradient every generated app ships; use the product's own colours"),
    ("bounce", r"cubic-bezier\(\s*[\d.]+\s*,\s*(1\.[1-9]|[2-9])|cubic-bezier\([^)]*,\s*-0?\.\d+\s*\)|\b(ease-bounce|animate-bounce|elastic)\b",
     "bouncy or overshooting easing; business software moves with short ease-out transitions"),
    ("gray-on-colour", r"\b(bg-(red|green|blue|indigo|purple|violet|emerald|teal|orange|rose)-[5-9]00)\b[^\"'`]*\btext-(gray|slate|zinc|neutral)-[3-6]00\b|"
                       r"\btext-(gray|slate|zinc|neutral)-[3-6]00\b[^\"'`]*\b(bg-(red|green|blue|indigo|purple|violet|emerald|teal|orange|rose)-[5-9]00)\b",
     "grey text on a saturated background fails contrast; use white or the design system's on-colour"),
    ("side-stripe", r"\bborder-l-(4|8)\b[^\"'`]*\brounded(-\w+)?\b|\brounded(-\w+)?\b[^\"'`]*\bborder-l-(4|8)\b|border-left\s*:\s*[3-9]px",
     "a thick coloured stripe on the side of a rounded card; use the table row, a status badge or a heading instead"),
    ("glass", r"\bbackdrop-blur(-\w+)?\b[^\"'`]*\bbg-white/(5|10|20)\b|\bbg-white/(5|10|20)\b[^\"'`]*\bbackdrop-blur",
     "frosted-glass panels; data screens need solid surfaces so numbers stay legible"),
]
COMPILED = [(k, re.compile(rx, re.I), why) for k, rx, why in RULES]


def is_style_file(path):
    return os.path.splitext(path or "")[1].lower() in STYLE_EXT


def lint(text, limit=8):
    found = []
    for n, line in enumerate((text or "").splitlines(), 1):
        if IGNORE.search(line):
            continue
        for key, rx, why in COMPILED:
            m = rx.search(line)
            if m:
                found.append({"line": n, "rule": key, "match": m.group(0)[:60], "why": why})
                break
        if len(found) >= limit:
            break
    return found


def advice(path, text):
    if not is_style_file(path):
        return ""
    found = lint(text)
    if not found:
        return ""
    rows = "\n".join(f"- line {f['line']} ({f['rule']}): `{f['match']}`: {f['why']}" for f in found)
    return (f"Design check on {os.path.basename(path)}: {len(found)} visual habit(s) that make an interface look "
            f"machine-made. Fix them with the frontend-quality skill, or keep one deliberately with "
            f"`design-lint-ignore: <reason>` on that line:\n{rows}")
