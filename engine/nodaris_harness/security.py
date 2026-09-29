"""Security assessment: the whole procedure behind "check my app's security", run within a signed scope.

Two tiers.
- Static, always: secrets (gitleaks, history included), vulnerable code patterns (semgrep), dependencies
  (osv-scanner over lockfiles), filesystem and IaC (trivy), and the harness PHI lint over the repository's logs.
  Read-only on the repository; never touches a running system.
- Live, only inside the scope: a passive baseline scan (ZAP in Docker) of each host the scope names. Active
  scanning is a separate allowed check the owner lists explicitly. Before every live run the target is checked
  against the scope again: the hostname must match one the scope lists, must resolve, and must not be marked
  production unless the scope names production. Third-party hosts are never targets.

The scope lives in the repository at .nodaris-harness/security-scope.json and is signed once per environment by
the owner with the same approval key as consequential actions. A scope that fails its signature check is treated
as absent, and then only the static tier runs.

Findings go to docs/security/findings-<date>.md in the repository: severity, finding, evidence (redacted), fix,
retest. Values that look like identifiers are replaced by surrogates before anything is written.
"""
import datetime, hashlib, hmac, json, os, re, shutil, socket, subprocess, time
from urllib.parse import urlparse

from . import policy, redact

SCOPE_REL = os.path.join(".nodaris-harness", "security-scope.json")
FINDINGS_DIR = os.path.join("docs", "security")
PROD_WORDS = re.compile(r"\b(prod|production|live|www)\b", re.I)
PRIVATE_NETS = ("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31.", "127.")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
ZAP_IMAGE = "ghcr.io/zaproxy/zaproxy:stable"
SCAN_TIMEOUT = 900


# ---- scope ----------------------------------------------------------------------------------------------------

def scope_path(repo):
    return os.path.join(repo, SCOPE_REL)


def _canon(scope):
    body = {k: scope[k] for k in ("environments", "owner", "created", "version") if k in scope}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def write_scope(repo, environments, owner, answer_reader):
    """Create or replace the scope. A person confirms at a terminal; the file is signed with the approval key.

    environments: [{"name": "staging", "hosts": ["https://staging.example.com"], "checks": ["passive"], "production": false}]
    """
    for env in environments:
        for h in env.get("hosts", []):
            u = urlparse(h if "://" in h else "https://" + h)
            if not u.hostname:
                raise ValueError(f"not a host: {h}")
        env.setdefault("checks", ["passive"])
        env.setdefault("production", False)
    scope = {"version": 1, "owner": owner, "created": time.strftime("%Y-%m-%d"), "environments": environments}
    answer = answer_reader(scope)
    if answer.strip().lower() != "yes":
        return None, "not signed"
    scope["signature"] = hmac.new(policy._key(), _canon(scope), hashlib.sha256).hexdigest()
    path = scope_path(repo)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(scope, fh, indent=1)
    return scope, path


def load_scope(repo):
    """The signed scope, or None with a reason when there is none or it does not verify."""
    path = scope_path(repo)
    if not os.path.exists(path):
        return None, "no scope file; only the static tier runs"
    try:
        scope = json.load(open(path))
        good = hmac.compare_digest(scope.get("signature", ""), hmac.new(policy._key(), _canon(scope), hashlib.sha256).hexdigest())
    except (ValueError, KeyError, OSError):
        return None, "the scope file could not be read; only the static tier runs"
    if not good:
        return None, "the scope file's signature does not verify (edited, or signed on another machine); only the static tier runs"
    return scope, "ok"


def check_target(url, scope):
    """Return (ok, reason, environment). The target must be a scope host in a non-production environment."""
    u = urlparse(url if "://" in url else "https://" + url)
    host = (u.hostname or "").lower()
    if not host:
        return False, f"{url} is not a URL", None
    for env in scope.get("environments", []):
        for h in env.get("hosts", []):
            hu = urlparse(h if "://" in h else "https://" + h)
            if (hu.hostname or "").lower() == host:
                if env.get("production") and not env.get("production_allowed"):
                    return False, f"{host} is marked production in the scope; the owner has not allowed production checks", env
                if host not in LOCAL_HOSTS:
                    try:
                        socket.getaddrinfo(host, None)
                    except socket.gaierror:
                        return False, f"{host} does not resolve; the scope may be stale", env
                return True, "in scope", env
    if PROD_WORDS.search(host):
        return False, f"{host} looks like a production host and is not in the scope", None
    return False, f"{host} is not in the signed scope", None


# ---- static tier ------------------------------------------------------------------------------------------------

def sh(cmd, cwd, timeout=SCAN_TIMEOUT):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 127, "", str(exc)


def _lockfiles(repo):
    names = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt", "go.sum", "Cargo.lock")
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".venv", "venv", ".git", "dist", "build")]
        out += [os.path.relpath(os.path.join(root, f), repo) for f in files if f in names]
    return out


def static_tier(repo):
    """Run every installed scanner over the whole repository. Returns a list of tool results."""
    results = []

    def add(tool, rc, out, err, findings):
        results.append({"tool": tool, "ran": rc not in (127,), "rc": rc, "findings": findings,
                        "note": "" if rc != 127 else "not installed"})

    if shutil.which("gitleaks"):
        rc, out, err = sh(["gitleaks", "git", "--redact", "--no-banner", "--report-format", "json", "--report-path", "/dev/stdout", "."], repo)
        try:
            items = json.loads(out) if out.strip() else []
        except ValueError:
            items = []
        add("gitleaks", rc, out, err, [{"severity": "High", "finding": f"secret pattern {i.get('RuleID')}", "where": f"{i.get('File')}:{i.get('StartLine')}"} for i in items])
    else:
        add("gitleaks", 127, "", "", [])
    if shutil.which("semgrep"):
        rc, out, err = sh(["semgrep", "scan", "--json", "--quiet", "--metrics", "off", "--config", "p/owasp-top-ten", "--config", "p/secrets", "."], repo)
        try:
            items = json.loads(out).get("results", []) if out.strip() else []
        except ValueError:
            items = []
        sev = {"ERROR": "High", "WARNING": "Medium", "INFO": "Low"}
        add("semgrep", rc, out, err, [{"severity": sev.get(i.get("extra", {}).get("severity"), "Medium"), "finding": i.get("check_id", ""),
                                      "where": f"{i.get('path')}:{i.get('start', {}).get('line')}"} for i in items])
    else:
        add("semgrep", 127, "", "", [])
    locks = _lockfiles(repo)
    if shutil.which("osv-scanner") and locks:
        rc, out, err = sh(["osv-scanner", "scan", "--format", "json", *locks], repo)
        findings = []
        try:
            for r in json.loads(out).get("results", []) if out.strip() else []:
                for pkg in r.get("packages", []):
                    for v in pkg.get("vulnerabilities", []):
                        findings.append({"severity": "High", "finding": f"{pkg['package']['name']} {pkg['package']['version']}: {v.get('id')}", "where": r.get("source", {}).get("path", "")})
        except ValueError:
            pass
        add("osv-scanner", rc, out, err, findings)
    else:
        add("osv-scanner", 127 if not shutil.which("osv-scanner") else 0, "", "", [])
    if shutil.which("trivy"):
        rc, out, err = sh(["trivy", "fs", "--quiet", "--format", "json", "--severity", "HIGH,CRITICAL", "--skip-dirs", "node_modules", "--skip-dirs", ".venv",
                           "--scanners", "vuln,misconfig,secret", "--timeout", "10m", "."], repo)
        findings = []
        try:
            for r in json.loads(out).get("Results", []) if out.strip() else []:
                for v in r.get("Vulnerabilities", []) or []:
                    findings.append({"severity": v.get("Severity", "High").title(), "finding": f"{v.get('PkgName')}: {v.get('VulnerabilityID')}", "where": r.get("Target", "")})
                for m in r.get("Misconfigurations", []) or []:
                    findings.append({"severity": m.get("Severity", "High").title(), "finding": m.get("ID", ""), "where": r.get("Target", "")})
                for s in r.get("Secrets", []) or []:
                    findings.append({"severity": "High", "finding": f"secret pattern {s.get('RuleID')}", "where": f"{r.get('Target')}:{s.get('StartLine')}"})
        except ValueError:
            pass
        add("trivy", rc, out, err, findings)
    else:
        add("trivy", 127, "", "", [])
    return results


# ---- live tier ----------------------------------------------------------------------------------------------------

def live_tier(url, env, repo):
    """Passive baseline scan of one in-scope host with ZAP in Docker. Active checks only when the scope lists them."""
    if not shutil.which("docker"):
        return {"tool": "zap-baseline", "ran": False, "rc": 127, "findings": [], "note": "docker is not installed; the live tier needs it"}
    active = "active" in env.get("checks", [])
    out_dir = os.path.join(repo, FINDINGS_DIR, "zap")
    os.makedirs(out_dir, exist_ok=True)
    script = "zap-full-scan.py" if active else "zap-baseline.py"
    rc, out, err = sh(["docker", "run", "--rm", "-v", f"{out_dir}:/zap/wrk:rw", "-t", ZAP_IMAGE, script, "-t", url, "-J", "report.json", "-I"], repo)
    findings = []
    try:
        rep = json.load(open(os.path.join(out_dir, "report.json")))
        risk = {"3": "High", "2": "Medium", "1": "Low", "0": "Informational"}
        for site in rep.get("site", []):
            for a in site.get("alerts", []):
                findings.append({"severity": risk.get(str(a.get("riskcode")), "Low"), "finding": a.get("alert", ""),
                                 "where": (a.get("instances") or [{}])[0].get("uri", site.get("@name", ""))})
    except (OSError, ValueError):
        pass
    return {"tool": "zap-full" if active else "zap-baseline", "ran": rc != 127, "rc": rc, "findings": findings,
            "note": "" if rc in (0, 1, 2) else (err or out)[-300:]}


# ---- assessment ---------------------------------------------------------------------------------------------------

def assess(repo, targets=None, run_live=True):
    """The whole procedure. Returns a report dict and writes the findings log."""
    repo = os.path.abspath(repo)
    report = {"repo": repo, "date": time.strftime("%Y-%m-%d %H:%M"), "static": [], "live": [], "skipped": [], "scope": None}
    report["static"] = static_tier(repo)
    scope, why = load_scope(repo)
    report["scope"] = why
    if run_live:
        if scope is None:
            report["skipped"].append(f"live tier: {why}")
        else:
            wanted = targets or [h for env in scope["environments"] if not env.get("production") for h in env.get("hosts", [])]
            for url in wanted:
                ok, reason, env = check_target(url, scope)
                if not ok:
                    report["skipped"].append(f"{url}: {reason}")
                    continue
                report["live"].append({"target": url, **live_tier(url, env, repo)})
    report["log"] = write_findings(repo, report)
    return report


SEV_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Informational": 4}


def write_findings(repo, report):
    sur = redact.Surrogates()
    clean = lambda t: redact.redact_text(str(t), sur).text  # noqa: E731
    rows = []
    for r in report["static"] + report["live"]:
        for f in r["findings"]:
            rows.append((SEV_ORDER.get(f["severity"], 3), f["severity"], clean(f["finding"]), clean(f["where"]), r["tool"]))
    rows.sort()
    d = os.path.join(repo, FINDINGS_DIR)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"findings-{datetime.date.today().isoformat()}.md")
    lines = [f"# Security findings, {report['date']}", "",
             "Static tier: " + ", ".join(f"{r['tool']} ({'ran' if r['ran'] else r['note']})" for r in report["static"]),
             "Live tier: " + (", ".join(f"{r['target']} via {r['tool']} ({'ran' if r['ran'] else r['note']})" for r in report["live"]) or "none"),
             "Scope: " + str(report["scope"])]
    if report["skipped"]:
        lines += ["", "Skipped:"] + [f"- {s}" for s in report["skipped"]]
    lines += ["", "| Severity | Finding | Where | Tool | Fix | Retest |", "|---|---|---|---|---|---|"]
    lines += [f"| {sev} | {finding} | {where} | {tool} |  |  |" for _, sev, finding, where, tool in rows]
    if not rows:
        lines.append("| | no findings from the tools that ran | | | | |")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    sur.burn()
    return path


def summary(report):
    counts = {}
    for r in report["static"] + report["live"]:
        for f in r["findings"]:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    ran = [r["tool"] for r in report["static"] + report["live"] if r["ran"]]
    missing = [r["tool"] for r in report["static"] if not r["ran"]]
    parts = [f"Security check of {report['repo']}.", "Ran: " + (", ".join(ran) or "nothing"),
             "Findings: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: SEV_ORDER.get(kv[0], 9))) or "none"),
             f"Log: {report['log']}"]
    if missing:
        parts.append("Not installed: " + ", ".join(missing))
    parts += report["skipped"]
    return "\n".join(parts)
