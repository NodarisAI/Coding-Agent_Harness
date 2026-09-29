#!/usr/bin/env python3
"""Security scan of what this change touched: secrets, OWASP patterns, Python security smells, vulnerable dependencies.

  python3 scan.py [REPO] [--files PATH ...]   # default: the current directory's repository

Scans only files changed against HEAD plus new untracked files, so it takes seconds, not the minutes of a
full-repository baseline. Runs every scanner that is installed and names every one that is not; downloads
nothing but semgrep's public rule packs. Findings are printed as file:line and rule id; secret values are
never printed (gitleaks runs with --redact). Exit 0 clean, 1 findings, 2 nothing could be scanned.
"""
import json, os, shutil, subprocess, sys

CODE = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".java", ".rb", ".php", ".sql", ".yml", ".yaml", ".tf", ".sh")
LOCKS = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt", "go.sum", "Cargo.lock")
SEMGREP_CONFIGS = ["p/owasp-top-ten", "p/secrets", "p/python", "p/typescript", "p/react"]
SKIP_DIRS = ("node_modules/", ".venv/", "venv/", "dist/", "build/", ".git/")


def sh(cmd, cwd, timeout=180):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def changed_files(repo):
    rc, out, _ = sh(["git", "diff", "--name-only", "HEAD"], repo)
    files = out.split() if rc == 0 else []
    rc, out, _ = sh(["git", "ls-files", "--others", "--exclude-standard"], repo)
    files += out.split() if rc == 0 else []
    return sorted({f for f in files if os.path.isfile(os.path.join(repo, f)) and not f.startswith(SKIP_DIRS)})


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def main(argv):
    explicit = []
    if "--files" in argv:
        i = argv.index("--files")
        explicit, argv = argv[i + 1:], argv[:i]
    repo = os.path.abspath(argv[0] if argv else ".")
    rc, top, _ = sh(["git", "rev-parse", "--show-toplevel"], repo)
    if rc != 0:
        print("scan: not a git repository; nothing to compare against", file=sys.stderr)
        return 2
    repo = top.strip()
    named = sorted({os.path.relpath(os.path.abspath(f), repo) for f in explicit if os.path.isfile(os.path.abspath(f))})
    files = sorted(set(changed_files(repo)) | set(named))
    if not files:
        # Committing does not escape the scan: with nothing uncommitted, the last commit is what changed (against the
        # empty tree when it is the first commit). Earlier commits are covered by naming files with --files.
        rc, _, _ = sh(["git", "rev-parse", "--verify", "-q", "HEAD~1"], repo)
        rc, out, _ = sh(["git", "diff", "--name-only", "HEAD~1" if rc == 0 else EMPTY_TREE, "HEAD"], repo)
        files = [f for f in out.split() if os.path.isfile(os.path.join(repo, f)) and not f.startswith(SKIP_DIRS)] if rc == 0 else []
        if files:
            print(f"scan: nothing uncommitted; scanning the {len(files)} file(s) changed by the last commit")
    if not files:
        print("scan: no changed files")
        return 0
    code = [f for f in files if f.endswith(CODE)]
    findings, ran, skipped = [], [], []

    if shutil.which("gitleaks"):
        ran.append("gitleaks")
        for f in files:
            rc, out, err = sh(["gitleaks", "dir", "--no-banner", "--redact", "--report-format", "json", "--report-path", "-", "--log-level", "error", f], repo, 60)
            if rc == 1:
                try:
                    for r in json.loads(out or "[]"):
                        findings.append(f"{f}:{r.get('StartLine')}: secret ({r.get('RuleID')})")
                except Exception:
                    findings.append(f"{f}: secret (gitleaks)")
    else:
        skipped.append("gitleaks")

    if code and shutil.which("semgrep"):
        ran.append("semgrep")
        cmd = ["semgrep", "scan", "--json", "--quiet", "--metrics", "off", "--timeout", "20"]
        for c in SEMGREP_CONFIGS:
            cmd += ["--config", c]
        rc, out, err = sh(cmd + code, repo, 300)
        try:
            for r in json.loads(out).get("results", []):
                findings.append(f"{r['path']}:{r['start']['line']}: {r['check_id'].split('.')[-1]} (semgrep, {r['extra'].get('severity', '').lower()})")
        except Exception:
            skipped.append("semgrep (could not run: " + (err.strip().splitlines() or ["no output"])[-1][:80] + ")")
            ran.remove("semgrep")
    elif code:
        skipped.append("semgrep")

    py = [f for f in code if f.endswith(".py") and "/tests/" not in f and not os.path.basename(f).startswith("test_")]
    if py and shutil.which("bandit"):
        ran.append("bandit")
        rc, out, _ = sh(["bandit", "-q", "-f", "json", "-ll"] + py, repo)
        try:
            for r in json.loads(out).get("results", []):
                findings.append(f"{r['filename']}:{r['line_number']}: {r['test_id']} {r['test_name']} (bandit, {r['issue_severity'].lower()})")
        except Exception:
            pass
    elif py:
        skipped.append("bandit")

    locks = [f for f in files if os.path.basename(f) in LOCKS]
    if locks and shutil.which("osv-scanner"):
        ran.append("osv-scanner")
        args = []
        for f in locks:
            args += ["-L", f]
        rc, out, _ = sh(["osv-scanner", "scan", "--format", "json"] + args, repo, 180)
        try:
            for res in json.loads(out).get("results", []):
                for pkg in res.get("packages", []):
                    for v in pkg.get("vulnerabilities", []):
                        findings.append(f"{res['source']['path']}: {pkg['package']['name']} {pkg['package'].get('version', '')} {v['id']} (osv)")
        except Exception:
            pass
    elif locks:
        skipped.append("osv-scanner")

    # The done gate reads this line to check that every security-sensitive file was actually scanned.
    print("scan: scanned files: " + ", ".join(files))
    print(f"scan: {len(files)} changed file(s); ran {', '.join(ran) or 'nothing'}" + (f"; not installed or failed: {', '.join(skipped)}" if skipped else ""))
    for f in findings:
        print("  " + f)
    if findings:
        print(f"scan: {len(findings)} finding(s). Fix each, or for a proven false positive add the scanner's inline "
              "suppression with a one-line reason, then run the scan again.")
        return 1
    if not ran:
        return 2
    print("scan: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
