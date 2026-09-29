"""Onboarding: who this person is, which packs and agents they use, and which plugins their company recommends.

The answers live in <harness home>/settings.json. hosts.selected_packs() reads the packs from there, the installer
reads the hosts, and the agent reads the reply style and the subagent budget. Plugin discovery only suggests: it
never installs anything. Installing happens only after the person says yes to that plugin, by running the exact
commands shown to them, one at a time, with each exit code reported.
"""
import argparse, base64, datetime, getpass, json, os, re, shlex, shutil, subprocess, sys

from . import policy

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTRY = os.path.join(ENGINE_ROOT, "packs", "registry.json")
VERSION = 1

USE_OPTIONS = [("healthcare", "Healthcare apps", "Claims, billing or clinical software that may touch patient data."),
               ("creative", "Websites and motion", "Marketing sites, product video, motion and visual assets."),
               ("general", "General software", "Any other software work.")]
ROLE_OPTIONS = [("engineer", "Engineer", "You write and review code and own how it runs."),
                ("builder", "Builder", "You build products while the agent does most of the coding."),
                ("practice-staff", "Practice staff", "You work in a practice and use the agent for everyday tasks.")]
STYLE_OPTIONS = [("brief", "Brief", "The result first, in a few plain sentences."),
                 ("explain", "Explain", "What was done, why it matters and what changes for you."),
                 ("teach", "Teach", "Step by step, with each new term defined.")]
PLAN_OPTIONS = [("pro", "Pro", "Claude Pro. Smaller agent budget per session."),
                ("max", "Max", "Claude Max. The largest agent budget per session."),
                ("team", "Team", "A Claude Team or Enterprise seat."),
                ("api", "API", "Pay-as-you-go API billing.")]
HOST_OPTIONS = [("claude", "Claude Code", "claude"), ("codex", "Codex", "codex"), ("gemini", "Gemini CLI", "gemini"),
                ("cursor", "Cursor", "cursor-agent"), ("opencode", "OpenCode", "opencode")]

ROLES = tuple(v for v, _, _ in ROLE_OPTIONS)
USES = tuple(v for v, _, _ in USE_OPTIONS)
STYLES = tuple(v for v, _, _ in STYLE_OPTIONS)
PLANS = tuple(v for v, _, _ in PLAN_OPTIONS)
HOSTS = tuple(v for v, _, _ in HOST_OPTIONS)
PACKS = ("core", "healthcare", "creative")
BUDGETS = {"pro": 150000, "max": 600000, "team": 400000, "api": 300000}
PLUGIN_STATUSES = ("suggested", "accepted", "declined", "installed", "failed")
TEAM_REPO = "NodarisAI/Nodaris-Memory-Vault"
TEAM_SYNC_TEXT = ("Team sync shares your redacted lessons, the changes the learner makes to your profile and anonymous "
                  "usage counts with the Nodaris memory vault once a day, and brings the rest of the team's lessons "
                  "back into your recall, so everyone's agent learns from the same mistakes. It never shares code, "
                  "prompts, file contents or patient data.")
UNVERIFIED = "found by search, not verified"


class SettingsError(ValueError):
    def __init__(self, field, message):
        super().__init__(f"{field}: {message}")
        self.field = field


# ---- settings --------------------------------------------------------------------------------------------------

def settings_path():
    return os.path.join(policy.home(), "settings.json")


def load():
    try:
        with open(settings_path()) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def is_onboarded():
    s = load()
    if not s:
        return False
    try:
        validate(s)
    except SettingsError:
        return False
    return True


def offer_path():
    return os.path.join(policy.home(), "onboarding-offered")


def claim_first_offer():
    """True exactly once per install: for the first session that finds onboarding unfinished. Later sessions, prompts
    and tool calls never offer it again; the person can run `nodaris-harness onboard` whenever they want. The marker
    is created atomically, so two sessions opened at the same moment cannot both offer it."""
    path = offer_path()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return False
    with os.fdopen(fd, "w") as fh:
        fh.write(datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat() + "\n")
    return True


def forget_offer():
    """Called by a full uninstall, so the next install is a new first use."""
    try:
        os.remove(offer_path())
    except OSError:
        pass


def save(settings):
    validate(settings)
    path = settings_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(settings, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def reset():
    path = settings_path()
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def default_budget(plan):
    return BUDGETS[plan]


def packs_for(uses):
    """core always; healthcare and creative follow the uses, and several uses union their packs."""
    out = ["core"]
    if "healthcare" in uses:
        out.append("healthcare")
    if "creative" in uses:
        out.append("creative")
    return out


def _norm(text):
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return re.sub(r"[,.]?\s+(inc|llc|ltd|corp|corporation|co|gmbh|plc)\.?$", "", t)


def is_nodaris_company(company):
    entry = registry_match(company)
    return bool(entry and entry["id"] == "nodaris")


def team_handle(runner=None):
    """The local part of `git config user.email`, or the OS user name. Read-only."""
    run = runner or subprocess.run
    email = ""
    try:
        p = run(["git", "config", "--get", "user.email"], capture_output=True, text=True, timeout=10)
        email = (p.stdout or "").strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        email = ""
    raw = email.split("@", 1)[0] if email else getpass.getuser()
    return re.sub(r"[^a-z0-9._-]", "", raw.lower()) or "member"


def _pick(field, value, table):
    """Accept a value, or an option label as shown in a question, case-insensitively."""
    v = str(value).strip()
    for val, label, *_ in table:
        if v.lower() in (val.lower(), label.lower()):
            return val
    raise SettingsError(field, f"{value!r} is not one of {', '.join(val for val, *_ in table)}")


def _pick_many(field, values, table):
    if isinstance(values, str):
        values = [x for x in re.split(r"\s*,\s*", values) if x]
    if not isinstance(values, list):
        raise SettingsError(field, "must be a list")
    out = []
    for v in values:
        val = _pick(field, v, table)
        if val not in out:
            out.append(val)
    return out


def _bool(field, value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("yes", "true", "y", "1"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("no", "false", "n", "0"):
        return False
    raise SettingsError(field, "must be yes or no")


def detected_hosts(which=None):
    which = which or shutil.which
    return [val for val, _, binary in HOST_OPTIONS if which(binary)]


def build(answers, which=None, runner=None, now=None):
    """Turn answers (values or option labels) into complete, validated settings. Raises SettingsError naming the field."""
    if not isinstance(answers, dict):
        raise SettingsError("answers", "must be a JSON object")
    company = str(answers.get("company") or "").strip()
    if not company:
        raise SettingsError("company", "is required")
    is_nodaris = is_nodaris_company(company)
    if "is_nodaris" in answers and answers["is_nodaris"] is not None:
        is_nodaris = _bool("is_nodaris", answers["is_nodaris"]) or is_nodaris
    uses = _pick_many("use", answers["use"], USE_OPTIONS) if answers.get("use") else (
        ["healthcare"] if is_nodaris else ["general"])
    if not uses:
        raise SettingsError("use", "choose at least one")
    if answers.get("packs"):
        packs = _pick_many("packs", answers["packs"], [(p, p) for p in PACKS])
        packs = ["core"] + [p for p in packs if p != "core"]
    else:
        packs = packs_for(uses)
    role = _pick("role", answers.get("role") or "engineer", ROLE_OPTIONS)
    if answers.get("hosts"):
        hosts = _pick_many("hosts", answers["hosts"], [(v, l) for v, l, _ in HOST_OPTIONS])
    else:
        hosts = detected_hosts(which) or ["claude"]
    style = _pick("reply_style", answers.get("reply_style") or "explain", STYLE_OPTIONS)
    plan = _pick("plan", answers.get("plan") or "max", PLAN_OPTIONS)
    budget = answers.get("subagent_budget_tokens")
    budget = default_budget(plan) if budget in (None, "") else budget
    ts = answers.get("team_sync")
    if isinstance(ts, (bool, str)) or ts is None:
        enabled = _bool("team_sync", ts) if ts is not None else False
        ts = {"enabled": enabled}
    if not isinstance(ts, dict):
        raise SettingsError("team_sync", "must be yes, no or an object")
    team = {"enabled": _bool("team_sync.enabled", ts.get("enabled", False)),
            "repo": str(ts.get("repo") or (TEAM_REPO if is_nodaris else "")),
            "handle": str(ts.get("handle") or (team_handle(runner) if is_nodaris or ts.get("enabled") else ""))}
    plugins = answers.get("plugins") or []
    settings = {"version": VERSION, "company": company, "is_nodaris": is_nodaris, "role": role, "use": uses,
                "packs": packs, "hosts": hosts, "reply_style": style, "plan": plan, "subagent_budget_tokens": budget,
                "team_sync": team, "plugins": plugins,
                "reversible_delete": _bool("reversible_delete", answers.get("reversible_delete", True)),
                "branch_rule": _bool("branch_rule", answers.get("branch_rule", True)),
                "deny_rules": _bool("deny_rules", answers.get("deny_rules", True)),
                "statusline": _bool("statusline", answers.get("statusline", True)),
                "onboarded_at": answers.get("onboarded_at") or (now or datetime.datetime.now(datetime.timezone.utc))
                .replace(microsecond=0).isoformat()}
    validate(settings)
    return settings


def validate(s):
    """Check a complete settings object. Raises SettingsError naming the first bad field."""
    if not isinstance(s, dict):
        raise SettingsError("settings", "must be a JSON object")
    if s.get("version") != VERSION:
        raise SettingsError("version", f"must be {VERSION}")
    if not isinstance(s.get("company"), str) or not s["company"].strip():
        raise SettingsError("company", "is required")
    if len(s["company"]) > 120:
        raise SettingsError("company", "must be 120 characters or fewer")
    if not isinstance(s.get("is_nodaris"), bool):
        raise SettingsError("is_nodaris", "must be true or false")
    if s.get("role") not in ROLES:
        raise SettingsError("role", f"must be one of {', '.join(ROLES)}")
    for field, allowed in (("use", USES), ("packs", PACKS), ("hosts", HOSTS)):
        v = s.get(field)
        if not isinstance(v, list) or not v or any(x not in allowed for x in v) or len(set(v)) != len(v):
            raise SettingsError(field, f"must be a non-empty list of distinct values from {', '.join(allowed)}")
    if "core" not in s["packs"]:
        raise SettingsError("packs", "must include core")
    if s.get("reply_style") not in STYLES:
        raise SettingsError("reply_style", f"must be one of {', '.join(STYLES)}")
    if s.get("plan") not in PLANS:
        raise SettingsError("plan", f"must be one of {', '.join(PLANS)}")
    b = s.get("subagent_budget_tokens")
    if isinstance(b, bool) or not isinstance(b, int) or not 10000 <= b <= 10000000:
        raise SettingsError("subagent_budget_tokens", "must be a whole number from 10000 to 10000000")
    t = s.get("team_sync")
    if not isinstance(t, dict) or not isinstance(t.get("enabled"), bool):
        raise SettingsError("team_sync", "must be an object with enabled true or false")
    for k in ("repo", "handle"):
        if not isinstance(t.get(k), str):
            raise SettingsError(f"team_sync.{k}", "must be text")
    if t["enabled"]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", t["repo"]):
            raise SettingsError("team_sync.repo", "must be owner/name when team sync is on")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,38}", t["handle"]):
            raise SettingsError("team_sync.handle", "must start with a lowercase letter or digit and be at most 39 "
                                                     "lowercase letters, digits, dots, dashes or underscores")
    p = s.get("plugins")
    if not isinstance(p, list):
        raise SettingsError("plugins", "must be a list")
    for i, item in enumerate(p):
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item.get(k) for k in ("name", "marketplace", "status")):
            raise SettingsError(f"plugins[{i}]", "needs name, marketplace and status")
        if item["status"] not in PLUGIN_STATUSES:
            raise SettingsError(f"plugins[{i}].status", f"must be one of {', '.join(PLUGIN_STATUSES)}")
        if not isinstance(item.get("install", []), list) or not all(isinstance(c, str) for c in item.get("install", [])):
            raise SettingsError(f"plugins[{i}].install", "must be a list of commands")
    if not isinstance(s.get("onboarded_at"), str):
        raise SettingsError("onboarded_at", "must be an ISO 8601 time")
    try:
        datetime.datetime.fromisoformat(s["onboarded_at"].replace("Z", "+00:00"))
    except ValueError:
        raise SettingsError("onboarded_at", "must be an ISO 8601 time")
    return True


# ---- company discovery -----------------------------------------------------------------------------------------

def load_registry(path=None):
    with open(path or REGISTRY) as fh:
        return json.load(fh)


def registry_match(company, registry=None):
    key = _norm(company)
    if not key:
        return None
    for e in (registry or load_registry())["entries"]:
        keys = {_norm(x) for x in e.get("names", []) + e.get("aliases", []) + e.get("domains", []) + [e["id"]]}
        if key in keys:
            return e
    return None


def _claude_plugins_dir():
    return os.path.join(os.path.expanduser("~"), ".claude", "plugins")


def known_marketplaces():
    """Marketplaces already added to Claude Code on this machine (read-only)."""
    try:
        with open(os.path.join(_claude_plugins_dir(), "known_marketplaces.json")) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _local_plugins(info):
    """Plugin names from a known marketplace's own marketplace.json, read only inside ~/.claude/plugins."""
    loc = os.path.realpath(str(info.get("installLocation") or ""))
    base = os.path.realpath(_claude_plugins_dir())
    if not loc.startswith(base + os.sep):
        return []
    try:
        with open(os.path.join(loc, ".claude-plugin", "marketplace.json")) as fh:
            return [p for p in json.load(fh).get("plugins", []) if isinstance(p, dict) and p.get("name")]
    except (OSError, ValueError):
        return []


def _source_text(info):
    src = info.get("source") or {}
    return str(src.get("repo") or src.get("url") or "")


def _suggestion(name, marketplace, description, install, verified, origin, note=""):
    return {"name": name, "marketplace": marketplace, "description": description, "install": install,
            "verified": verified, "origin": origin, "label": "" if verified else UNVERIFIED, "note": note}


def discover(company, search=False, which=None, runner=None, registry=None):
    """Plugin suggestions for a company: the registry, marketplaces already on this machine, then (only with search)
    a GitHub search. Returns a list of suggestions. Never installs anything."""
    which = which or shutil.which
    run = runner or subprocess.run
    out, seen = [], set()
    known = known_marketplaces() if which("claude") else {}

    def add(sug):
        k = (sug["name"], sug["marketplace"])
        if k not in seen:
            seen.add(k)
            out.append(sug)

    entry = registry_match(company, registry)
    if entry:
        mkt = entry["marketplace"]
        for p in entry.get("plugins", []):
            cmds = [c for c in p["install"] if not (mkt["name"] in known and " marketplace add " in f" {c} ")]
            note = "The marketplace is already added on this machine." if len(cmds) < len(p["install"]) else ""
            add(_suggestion(p["name"], mkt["name"], p.get("description", ""), cmds, bool(entry.get("verified")),
                            "registry", note))

    key = re.sub(r"[^a-z0-9]", "", _norm(company))
    if len(key) >= 3:
        for name, info in sorted(known.items()):
            hay = re.sub(r"[^a-z0-9]", "", (name + " " + _source_text(info)).lower())
            if key not in hay:
                continue
            plugins = _local_plugins(info)
            for p in plugins[:10]:
                add(_suggestion(p["name"], name, str(p.get("description", ""))[:160],
                                [f"claude plugin install {p['name']}@{name} --scope user"], True, "this machine",
                                "The marketplace is already added on this machine."))

    if search and which("gh") and len(key) >= 3:
        for sug in _search_github(company, run):
            add(sug)
    return out


def _search_github(company, run):
    q = f"{company} claude code plugin marketplace"
    try:
        p = run(["gh", "search", "repos", q, "--limit", "5", "--json", "fullName,description,stargazersCount,url"],
                capture_output=True, text=True, timeout=30)
        repos = json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    out = []
    for r in repos if isinstance(repos, list) else []:
        full = str(r.get("fullName") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full):
            continue
        try:
            m = run(["gh", "api", f"repos/{full}/contents/.claude-plugin/marketplace.json"], capture_output=True,
                    text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if m.returncode != 0:
            continue
        try:
            body = json.loads(base64.b64decode(json.loads(m.stdout).get("content", "")).decode())
            mkt = str(body.get("name") or "")
            plugins = [p for p in body.get("plugins", []) if isinstance(p, dict) and p.get("name")]
        except (ValueError, TypeError, AttributeError):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", mkt):
            continue
        desc = str(r.get("description") or "")[:160]
        for pl in plugins[:5]:
            name = str(pl["name"])
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                continue
            out.append(_suggestion(name, mkt, str(pl.get("description") or desc)[:160],
                                   [f"claude plugin marketplace add {full}",
                                    f"claude plugin install {name}@{mkt} --scope user"], False, "search",
                                   f"From github.com/{full} ({r.get('stargazersCount', 0)} stars)."))
    return out


_PLUGIN_ARG = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?(@[A-Za-z0-9_.-]+)?")


def _allowed_install(cmd):
    """Only `claude plugin marketplace add <owner/repo>` and `claude plugin install <name@marketplace> --scope user`,
    whatever a registry or a marketplace file on disk says."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if parts[:4] == ["claude", "plugin", "marketplace", "add"] and len(parts) == 5:
        return bool(_PLUGIN_ARG.fullmatch(parts[4]))
    if parts[:3] == ["claude", "plugin", "install"] and len(parts) == 6 and parts[4:] == ["--scope", "user"]:
        return bool(_PLUGIN_ARG.fullmatch(parts[3])) and not parts[3].startswith("-")
    return False


def install_plugin(sug, runner=None, stream=None):
    """Run a suggestion's exact commands one by one and report each exit code. Call only after the person said yes."""
    run = runner or subprocess.run
    s = stream or sys.stdout
    for cmd in sug["install"]:
        if not _allowed_install(cmd):
            s.write(f"Skipped, because it is not a plain plugin command: {cmd}\n")
            return False
        s.write(f"Running: {cmd}\n")
        s.flush()
        try:
            code = run(shlex.split(cmd), timeout=600).returncode
        except (OSError, subprocess.SubprocessError) as e:
            s.write(f"The command could not run: {e}\n")
            return False
        s.write(f"Exit code {code}.\n")
        if code != 0:
            return False
    return True


def describe(sug):
    lines = [f"{sug['name']} from the {sug['marketplace']} marketplace" + (f" ({sug['label']})" if sug["label"] else "")]
    if sug["description"]:
        lines.append(f"  {sug['description']}")
    if sug.get("note"):
        lines.append(f"  {sug['note']}")
    for c in sug["install"]:
        lines.append(f"  $ {c}")
    return "\n".join(lines)


# ---- Claude Code app flow --------------------------------------------------------------------------------------

def _opts(table):
    return [{"label": label, "description": desc} for _, label, desc in table]


def app_questions():
    """Two rounds shaped for Claude Code's AskUserQuestion tool: at most four questions, two to four options each."""
    round1 = [
        {"question": "How will you use the harness?", "header": "Use", "multiSelect": True, "options": _opts(USE_OPTIONS)},
        {"question": "Which best describes your role?", "header": "Role", "multiSelect": False,
         "options": _opts(ROLE_OPTIONS)},
        {"question": "Besides Claude Code, which coding agents should the harness connect to?", "header": "Agents",
         "multiSelect": True, "options": [{"label": l, "description": f"Install the harness into {l}."}
                                          for v, l, _ in HOST_OPTIONS if v != "claude"]},
        {"question": "Are you on the Nodaris team?", "header": "Team", "multiSelect": False,
         "options": [{"label": "Yes", "description": "Turns on the healthcare pack and offers team sync."},
                     {"label": "No", "description": "Use the harness on its own."}]},
    ]
    round2 = [
        {"question": "How should the agent write its replies to you?", "header": "Replies", "multiSelect": False,
         "options": _opts(STYLE_OPTIONS)},
        {"question": "Which Claude plan do you use?", "header": "Plan", "multiSelect": False,
         "options": _opts(PLAN_OPTIONS)},
        {"question": "Share redacted lessons with the Nodaris team? Only for Nodaris team members.", "header": "Team sync",
         "multiSelect": False,
         "options": [{"label": "Yes", "description": "Lessons, learner changes and anonymous counts; never code, prompts, files or patient data."},
                     {"label": "No", "description": "Nothing leaves this machine."}]},
    ]
    return [round1, round2]


def app_instructions(cli_path):
    q1, q2 = (json.dumps(r, separators=(",", ":")) for r in app_questions())
    example = json.dumps({"company": "Example Health", "is_nodaris": "No", "use": ["Healthcare apps"], "role": "Engineer",
                          "hosts": ["Claude Code", "Codex"], "reply_style": "Explain", "plan": "Max", "team_sync": "No"},
                         separators=(",", ":"))
    return (
        "The Nodaris harness is installed, but this person has not finished onboarding. Before any other work, "
        "onboard them:\n"
        f"1. Ask round 1 with the AskUserQuestion tool, using exactly these questions: {q1}\n"
        f"2. Ask round 2 the same way: {q2}. Skip the team sync question when they are not on the Nodaris team.\n"
        "3. Ask for their company name in plain chat.\n"
        f"4. Save the answers, using the option labels as given (Claude Code is always included in hosts): "
        f"`{cli_path} onboard --answers '<json>'`, for example {example}. If it reports a field error, ask again for that "
        "field only.\n"
        f"5. Run `{cli_path} onboard --discover \"<company>\"`. Offer each plugin it lists with its install commands. "
        "Run those exact commands only after the person says yes to that plugin, and report each exit code. "
        "Say plainly when a result is marked as found by search and not verified.\n"
        "If the person would rather skip it, stop and tell them they can run "
        f"`{cli_path} onboard` whenever they like. This is the only time the harness offers onboarding; it will not "
        "ask again in later sessions.\n"
        "Keep each message short and in plain sentences."
    )


# ---- terminal flow ---------------------------------------------------------------------------------------------

def _values(table, idxs):
    return [table[i][0] for i in idxs]


def interactive(ui=None, which=None, runner=None, show_splash=True, save_settings=True, stream=None):
    """The full onboarding in a terminal. Returns the saved settings, or None when the person did not confirm."""
    from . import tui
    ui = ui or tui
    which = which or shutil.which
    prev = load() or {}
    if show_splash:
        ui.splash(stream)
    ui.type_out("Welcome. A few questions set up the harness for the way you work; each one has a sensible default.",
                stream)
    ui.line("", stream)
    company = ui.ask("What is the name of your company?", prev.get("company", ""), stream, required=True)
    is_nodaris = is_nodaris_company(company)
    if not is_nodaris:
        is_nodaris = ui.confirm("Are you on the Nodaris team?", bool(prev.get("is_nodaris", False)), stream)
    pre_use = [i for i, (v, _, _) in enumerate(USE_OPTIONS) if v in (prev.get("use") or (["healthcare"] if is_nodaris else ["general"]))]
    use_idx = ui.choose_many("How will you use the harness?", [(l, d) for _, l, d in USE_OPTIONS], pre_use, stream)
    uses = _values(USE_OPTIONS, use_idx) or ["general"]
    role_default = next((i for i, (v, _, _) in enumerate(ROLE_OPTIONS) if v == prev.get("role")), 0)
    role = ROLE_OPTIONS[ui.choose("Which best describes your role?", [(l, d) for _, l, d in ROLE_OPTIONS],
                                  role_default, stream)][0]
    found = detected_hosts(which)
    host_opts = [(l, "Found on this machine." if v in found else "Not found on this machine.") for v, l, _ in HOST_OPTIONS]
    pre_hosts = [i for i, (v, _, _) in enumerate(HOST_OPTIONS) if v in (prev.get("hosts") or found or ["claude"])]
    hosts = _values(HOST_OPTIONS, ui.choose_many("Which coding agents should the harness connect to?", host_opts,
                                                 pre_hosts, stream)) or ["claude"]
    style_default = next((i for i, (v, _, _) in enumerate(STYLE_OPTIONS) if v == prev.get("reply_style")), 1)
    style = STYLE_OPTIONS[ui.choose("How should the agent write its replies to you?",
                                    [(l, d) for _, l, d in STYLE_OPTIONS], style_default, stream)][0]
    plan_default = next((i for i, (v, _, _) in enumerate(PLAN_OPTIONS) if v == prev.get("plan")), 1)
    plan = PLAN_OPTIONS[ui.choose("Which Claude plan do you use?",
                                  [(l, f"{d} Budget: {BUDGETS[v]:,} tokens.") for v, l, d in PLAN_OPTIONS],
                                  plan_default, stream)][0]
    team = False
    if is_nodaris:
        ui.panel("Team sync", TEAM_SYNC_TEXT, stream)
        team = ui.confirm("Turn on team sync?", bool((prev.get("team_sync") or {}).get("enabled", False)), stream)

    plugins = []
    search = False
    if which("gh") and not registry_match(company):
        search = ui.confirm(f"Search GitHub for Claude Code plugins published by {company}?", False, stream)
    suggestions = discover(company, search=search, which=which, runner=runner)
    for sug in suggestions:
        ui.panel("Suggested plugin", describe(sug).split("\n"), stream)
        yes = ui.confirm(f"Install {sug['name']}?", False, stream)
        plugins.append({"name": sug["name"], "marketplace": sug["marketplace"], "install": sug["install"],
                        "status": "accepted" if yes else "declined"})

    settings = build({"company": company, "is_nodaris": is_nodaris, "use": uses, "role": role, "hosts": hosts,
                      "reply_style": style, "plan": plan, "team_sync": team, "plugins": plugins}, which=which, runner=runner)
    ui.panel("Summary", summary_lines(settings), stream)
    if not ui.confirm("Save these settings?", True, stream):
        ui.line("Nothing was saved. Run the onboarding again when you are ready.", stream)
        return None
    if save_settings:
        save(settings)
        _install_accepted(settings, suggestions, runner, stream)
        ui.line(f"Settings saved to {settings_path()}.", stream)
    return settings


def _install_accepted(settings, suggestions, runner, stream):
    by_key = {(s["name"], s["marketplace"]): s for s in suggestions}
    changed = False
    for item in settings["plugins"]:
        if item["status"] != "accepted":
            continue
        ok = install_plugin(by_key.get((item["name"], item["marketplace"]), item), runner, stream)
        item["status"] = "installed" if ok else "failed"
        changed = True
    if changed:
        save(settings)


def summary_lines(s):
    label = lambda table, v: next((l for val, l, *_ in table if val == v), v)
    lines = [f"Company: {s['company']}" + (" (Nodaris team)" if s["is_nodaris"] else ""),
             f"Role: {label(ROLE_OPTIONS, s['role'])}",
             f"Uses: {', '.join(label(USE_OPTIONS, u) for u in s['use'])}",
             f"Packs: {', '.join(s['packs'])}",
             f"Coding agents: {', '.join(label(HOST_OPTIONS, h) for h in s['hosts'])}",
             f"Replies: {label(STYLE_OPTIONS, s['reply_style'])}",
             f"Plan: {label(PLAN_OPTIONS, s['plan'])}, agent budget {s['subagent_budget_tokens']:,} tokens per session",
             f"Team sync: {'on, ' + s['team_sync']['repo'] + ' as ' + s['team_sync']['handle'] if s['team_sync']['enabled'] else 'off'}"]
    if s["plugins"]:
        lines.append("Plugins: " + ", ".join(f"{p['name']} ({p['status']})" for p in s["plugins"]))
    return lines


def parse_answers(text):
    """--answers takes JSON text or a path to a JSON file."""
    t = (text or "").strip()
    if t and not t.startswith("{") and os.path.isfile(t):
        with open(t) as fh:
            t = fh.read()
    try:
        return json.loads(t)
    except ValueError as e:
        raise SettingsError("answers", f"is not valid JSON ({e.msg} at position {e.pos})")


def parser():
    ap = argparse.ArgumentParser(prog="nodaris-harness onboard")
    add_arguments(ap)
    return ap


def add_arguments(ap):
    ap.add_argument("--answers", help="JSON answers, or a path to a JSON file; saves without questions")
    ap.add_argument("--show", action="store_true", help="print the current settings")
    ap.add_argument("--discover", metavar="COMPANY", help="list plugin suggestions for a company; installs nothing")
    ap.add_argument("--search", action="store_true", help="allow a GitHub search during discovery")
    ap.add_argument("--reset", action="store_true", help="delete the saved settings")
    ap.add_argument("--no-motion", action="store_true", help="plain output with no animation")


def run_cli(args=None):
    """Entry point for `nodaris-harness onboard`. args is an argparse namespace or a list of strings."""
    a = args if isinstance(args, argparse.Namespace) else parser().parse_args(args)
    if getattr(a, "no_motion", False):
        os.environ["NODARIS_REDUCED_MOTION"] = "1"
    if getattr(a, "reset", False):
        print("Deleted the saved settings." if reset() else "There were no saved settings to delete.")
        if not (a.answers or a.discover):
            return 0
    if getattr(a, "show", False):
        s = load()
        if not s:
            print("This machine has not been onboarded yet. Run `nodaris-harness onboard` to start.")
            return 1
        print(json.dumps(s, indent=1))
        return 0
    if a.answers:
        try:
            s = build(parse_answers(a.answers))
            save(s)
        except SettingsError as e:
            print(f"The answers were not saved. {e.field}: {str(e).split(': ', 1)[1]}.", file=sys.stderr)
            return 2
        print(f"Settings saved to {settings_path()}.")
        for line in summary_lines(s):
            print("  " + line)
        if not a.discover:
            return 0
    if a.discover:
        found = discover(a.discover, search=a.search)
        if not found:
            print(f"No plugin suggestions were found for {a.discover}."
                  + ("" if a.search else " Add --search to look on GitHub."))
            return 0
        print(f"Plugin suggestions for {a.discover}. Nothing has been installed; run a plugin's commands only after "
              "the person agrees.")
        for sug in found:
            print("\n" + describe(sug))
        return 0
    from . import tui
    if not tui.can_prompt():
        print("The onboarding asks questions in a terminal. From an agent, pass the answers with --answers.",
              file=sys.stderr)
        return 2
    try:
        return 0 if interactive() else 1
    except KeyboardInterrupt:
        print("\nOnboarding was cancelled. Nothing was saved.")
        return 130
