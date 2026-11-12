#!/usr/bin/env python3
"""DDEV Dashboard: a local web UI for ddev.

Projects, start/stop, new projects (optionally from git), domains, settings, database tools,
add-ons, commands, logs and system status. Runs anywhere ddev runs (WSL, Linux, macOS,
Windows) with plain Python 3.8+, no extra packages.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import yaml  # optional; a small built-in parser is used when missing
except ImportError:
    yaml = None

__version__ = "1.0.0"

HOST = "127.0.0.1"
PORT = int(os.environ.get("DDEV_DASHBOARD_PORT", "8099"))
DEMO = os.environ.get("DDEV_DASHBOARD_DEMO") == "1"  # fake data, no ddev needed (screenshots, tests)
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.json")
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
ENV = {**os.environ, "DDEV_NO_TUI": "true", "DDEV_NONINTERACTIVE": "true", "GIT_TERMINAL_PROMPT": "0"}
POPEN_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}

PHP_VERSIONS = ["8.5", "8.4", "8.3", "8.2", "8.1", "8.0", "7.4", "7.3", "7.2", "7.1", "7.0", "5.6"]
WEBSERVERS = ["nginx-fpm", "apache-fpm"]
DATABASES = ["mariadb:11.8", "mariadb:10.11", "mariadb:10.6", "mysql:8.4", "mysql:8.0", "mysql:5.7",
             "postgres:17", "postgres:16"]
PROJECT_TYPES = ["php", "codeigniter", "laravel", "wordpress", "joomla", "drupal11", "drupal10",
                 "symfony", "cakephp", "craftcms", "magento2", "typo3", "shopware6", "silverstripe",
                 "backdrop", "modx", "generic"]
ENVIRONMENTS = ["auto", "wsl", "linux", "macos", "windows"]
LANGS = ["el", "en"]

# Quick commands. "types": project types it applies to (None = all); "needs": file that must exist.
COMMANDS = {
    "composer-install": {"args": ["composer", "install"], "needs": "composer.json"},
    "composer-update": {"args": ["composer", "update"], "needs": "composer.json"},
    "npm-install": {"args": ["npm", "install"], "needs": "package.json"},
    "npm-build": {"args": ["npm", "run", "build"], "needs": "package.json"},
    "artisan-migrate": {"args": ["artisan", "migrate", "--force"], "types": ["laravel"]},
    "artisan-optimize-clear": {"args": ["artisan", "optimize:clear"], "types": ["laravel"]},
    "spark-migrate": {"args": ["exec", "php", "spark", "migrate"], "types": ["codeigniter"]},
    "spark-cache-clear": {"args": ["exec", "php", "spark", "cache:clear"], "types": ["codeigniter"]},
    "wp-cache-flush": {"args": ["wp", "cache", "flush"], "types": ["wordpress"]},
    "wp-plugin-update": {"args": ["wp", "plugin", "update", "--all"], "types": ["wordpress"]},
    "wp-core-update": {"args": ["wp", "core", "update"], "types": ["wordpress"]},
    "drush-cr": {"args": ["drush", "cr"], "types": ["drupal", "drupal10", "drupal11", "drupal12"]},
    "drush-updb": {"args": ["drush", "updb", "-y"], "types": ["drupal", "drupal10", "drupal11", "drupal12"]},
    "symfony-cache-clear": {"args": ["exec", "php", "bin/console", "cache:clear"], "types": ["symfony"]},
}

# Popular official add-ons offered with one click (any "owner/repo" can also be typed in).
ADDONS = [
    "ddev/ddev-phpmyadmin", "ddev/ddev-adminer", "ddev/ddev-redis", "ddev/ddev-memcached",
    "ddev/ddev-solr", "ddev/ddev-elasticsearch", "ddev/ddev-opensearch", "ddev/ddev-mongo",
    "ddev/ddev-rabbitmq", "ddev/ddev-varnish", "ddev/ddev-cron", "ddev/ddev-selenium-standalone-chrome",
]

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$")
HOST_RE = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
DOCROOT_RE = re.compile(r"^[A-Za-z0-9_./-]*$")
SNAP_RE = re.compile(r"^(.+?)-(mysql|mariadb|postgres)_[\d.]+(\.gz|\.zst)?$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GIT_URL_RE = re.compile(r"^(https://|ssh://|git@)[^\s'\"]+$")

MESSAGES = {
    "busy": ("Τρέχει ήδη εργασία γι' αυτό το project", "A task is already running for this project"),
    "unknown_project": ("Άγνωστο project", "Unknown project"),
    "bad_name": ("Μη έγκυρο όνομα (γράμματα, αριθμοί, παύλες)", "Invalid name (letters, numbers, dashes)"),
    "exists": ("Υπάρχει ήδη project με αυτό το όνομα", "A project with this name already exists"),
    "has_ddev": ("Ο φάκελος έχει ήδη ρύθμιση DDEV", "This folder already has a DDEV config"),
    "not_empty": ("Για clone ο φάκελος πρέπει να μην υπάρχει ή να είναι άδειος",
                  "To clone, the folder must not exist or must be empty"),
    "bad_git": ("Μη έγκυρο git URL (https://, ssh:// ή git@)", "Invalid git URL (https://, ssh:// or git@)"),
    "bad_path": ("Η διαδρομή πρέπει να είναι πλήρης (π.χ. {ex})", "The path must be absolute (e.g. {ex})"),
    "bad_docroot": ("Μη έγκυρο docroot", "Invalid docroot"),
    "bad_value": ("Μη έγκυρη τιμή: {v}", "Invalid value: {v}"),
    "bad_domain": ("Μη έγκυρο domain: {v}", "Invalid domain: {v}"),
    "not_running": ("Το project πρέπει να τρέχει", "The project must be running"),
    "bad_snapshot_name": ("Μη έγκυρο όνομα snapshot", "Invalid snapshot name"),
    "unknown_snapshot": ("Άγνωστο snapshot", "Unknown snapshot"),
    "unknown_action": ("Άγνωστη ενέργεια", "Unknown action"),
    "bad_file": ("Δεκτά αρχεία: .sql, .sql.gz, .zip, .tar.gz κ.λπ.", "Accepted files: .sql, .sql.gz, .zip, .tar.gz etc."),
    "deleted": ("Διαγράφηκε {v}", "Deleted {v}"),
    "bad_command": ("Η εντολή δεν ταιριάζει σε αυτό το project", "This command doesn't apply to this project"),
    "bad_repo": ("Γράψε το add-on ως owner/repo, π.χ. ddev/ddev-redis", "Write the add-on as owner/repo, e.g. ddev/ddev-redis"),
    "unknown_addon": ("Το add-on δεν είναι εγκατεστημένο", "This add-on isn't installed"),
    "demo": ("Demo: οι ενέργειες είναι απενεργοποιημένες", "Demo: actions are disabled"),
    "skip_config": ("Το repo έχει ήδη ρύθμιση DDEV, τη χρησιμοποιώ όπως είναι.",
                    "The repo already has a DDEV config, using it as is."),
}

lock = threading.Lock()
busy = {}      # key -> {"action", "output", "started"}
last_log = {}  # key -> {"action", "ok", "output", "time"}
_cache = {}    # name -> (expires, value)


class BadRequest(Exception):
    pass


def cached(name, ttl, fn):
    now = time.time()
    hit = _cache.get(name)
    if hit and hit[0] > now:
        return hit[1]
    value = fn()
    _cache[name] = (now + ttl, value)
    return value


# ---------- settings / environment ----------

def detect_env():
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


DETECTED_ENV = detect_env()


def load_settings():
    s = {"lang": "el", "env": "auto", "projects_dir": ""}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s.update({k: v for k, v in json.load(f).items() if k in s})
    except (OSError, ValueError):
        pass
    return s


settings = load_settings()


def env():
    return DETECTED_ENV if settings["env"] == "auto" else settings["env"]


def msg(key, **kw):
    el, en = MESSAGES[key]
    return (en if settings["lang"] == "en" else el).format(**kw)


def wsl_distro():
    """Name of this WSL distro (for vscode-remote links), e.g. 'Ubuntu-24.04'."""
    if DETECTED_ENV != "wsl":
        return ""
    name = os.environ.get("WSL_DISTRO_NAME", "")
    if not name:
        try:
            out = subprocess.run(["wslpath", "-w", "/"], capture_output=True, timeout=5, **POPEN_TEXT).stdout
            m = re.match(r"^\\\\wsl(?:\.localhost|\$)\\([^\\]+)", out.strip())
            name = m.group(1) if m else ""
        except (OSError, subprocess.SubprocessError):
            pass
    return name


WSL_DISTRO = wsl_distro()


def to_host_path(path):
    """Turn what the user typed into a path ddev on this machine understands."""
    path = path.strip().strip('"')
    if env() == "windows":
        path = os.path.normpath(path)
        ok = re.match(r"^[A-Za-z]:\\", path) is not None
        example = r"D:\Projects\site"
    else:
        m = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
        if m and env() == "wsl":
            path = f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
        if path.startswith("~"):
            path = os.path.expanduser(path)
        path = os.path.normpath(path)
        ok = path.startswith("/")
        example = r"D:\Projects\site" if env() == "wsl" else "/home/me/projects/site"
    if not ok or "\0" in path:
        raise BadRequest(msg("bad_path", ex=example))
    return path


# ---------- tiny YAML reader (only what ddev's config.yaml needs) ----------

def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v in ("true", "false"):
        return v == "true"
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    return v


def simple_yaml(text):
    data, key = {}, None
    for raw in text.splitlines():
        line = raw.split(" #")[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if indent == 0 and ":" in s:
            key, _, val = s.partition(":")
            key = key.strip()
            data[key] = _scalar(val) if val.strip() else None
        elif key and s.startswith("- "):
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_scalar(s[2:]))
        elif key and ":" in s:
            if not isinstance(data.get(key), dict):
                data[key] = {}
            k, _, val = s.partition(":")
            data[key][k.strip()] = _scalar(val)
    return data


def read_config(approot):
    try:
        with open(os.path.join(approot, ".ddev", "config.yaml"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    return (yaml.safe_load(text) if yaml else simple_yaml(text)) or {}


# ---------- ddev helpers ----------

def run(argv, cwd=None, timeout=120):
    p = subprocess.run(argv, capture_output=True, timeout=timeout, cwd=cwd or HERE, env=ENV, **POPEN_TEXT)
    return p.returncode, p.stdout + p.stderr


def ddev(*args):
    return ["ddev", *args]


def json_raw(out):
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and '"raw"' in line:
            return json.loads(line).get("raw")
    return None


def phpmyadmin_url(p):
    path = os.path.join(p["approot"], ".ddev", "docker-compose.phpmyadmin.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r"HTTPS_EXPOSE=(\d+):", f.read())
    except OSError:
        return None
    base = p.get("httpsurl") or p.get("primary_url") or ""
    return f"{base}:{m.group(1)}" if m and base else None


def list_projects():
    if DEMO:
        return demo_data()["projects"]
    code, out = run(ddev("list", "--json-output"), timeout=60)
    raw = json_raw(out)
    if raw is None:
        raise RuntimeError(out.strip() or f"ddev list failed ({code})")
    projects = [p for p in raw if p.get("name") and p.get("approot")]
    for p in projects:
        p["phpmyadmin_url"] = phpmyadmin_url(p)
    return projects


def get_project(name):
    for p in list_projects():
        if p["name"] == name:
            return p
    raise BadRequest(msg("unknown_project"))


def snapshots(approot):
    d = os.path.join(approot, ".ddev", "db_snapshots")
    out = []
    try:
        for fn in os.listdir(d):
            m = SNAP_RE.match(fn)
            if m:
                st = os.stat(os.path.join(d, fn))
                out.append({"name": m.group(1), "size": st.st_size, "time": st.st_mtime})
    except OSError:
        pass
    return sorted(out, key=lambda s: -s["time"])


def installed_addons(approot):
    code, out = run(ddev("add-on", "list", "--installed", "-j"), cwd=approot, timeout=60)
    raw = json_raw(out) or []
    return [{"name": a.get("Name"), "repo": a.get("Repository"), "version": a.get("Version")}
            for a in raw if isinstance(a, dict) and a.get("Name")]


def commands_for(p_type, approot):
    out = []
    for cid, c in COMMANDS.items():
        if c.get("types") and p_type not in c["types"]:
            continue
        if c.get("needs") and not os.path.exists(os.path.join(approot, c["needs"])):
            continue
        out.append({"id": cid, "cmd": "ddev " + " ".join(c["args"])})
    return out


def as_list(v):
    return [str(x) for x in v] if isinstance(v, list) else []


def project_details(name):
    if DEMO:
        if name not in demo_data()["details"]:
            raise BadRequest(msg("unknown_project"))
        return demo_data()["details"][name]
    p = get_project(name)
    cfg = read_config(p["approot"])
    db = cfg.get("database") if isinstance(cfg.get("database"), dict) else {}
    p_type = cfg.get("type") or p.get("type")
    info = {
        "name": name,
        "approot": p["approot"],
        "status": p["status"],
        "type": p_type,
        "docroot": cfg.get("docroot") or "",
        "php_version": str(cfg.get("php_version") or ""),
        "webserver_type": cfg.get("webserver_type") or "",
        "database": f"{db.get('type', '?')}:{db.get('version', '?')}",
        "additional_hostnames": as_list(cfg.get("additional_hostnames")),
        "additional_fqdns": as_list(cfg.get("additional_fqdns")),
        "phpmyadmin": p["phpmyadmin_url"] is not None,
        "snapshots": snapshots(p["approot"]),
        "addons": installed_addons(p["approot"]),
        "commands": commands_for(p_type, p["approot"]),
        "xdebug": None,
    }
    if p["status"] == "running":
        code, out = run(ddev("xdebug", "status"), cwd=p["approot"], timeout=30)
        info["xdebug"] = "enabled" in out.lower() and "disabled" not in out.lower()
    return info


def default_projects_dir(projects):
    if settings["projects_dir"]:
        return settings["projects_dir"]
    parents = [os.path.dirname(p["approot"].rstrip("/\\")) for p in projects]
    if parents:  # the folder that holds most existing projects
        return max(set(parents), key=parents.count)
    return os.path.expanduser("~")


def clean_hosts(items, strip_suffix):
    out = []
    for h in items or []:
        h = str(h).strip().lower()
        if strip_suffix and h.endswith(".ddev.site"):
            h = h[: -len(".ddev.site")]
        if not h:
            continue
        if not HOST_RE.match(h):
            raise BadRequest(msg("bad_domain", v=h))
        if h not in out:
            out.append(h)
    return out


def pick(value, allowed):
    if value not in allowed:
        raise BadRequest(msg("bad_value", v=value))
    return value


def clean_docroot(v):
    v = str(v or "").strip().replace("\\", "/").strip("/")
    if not DOCROOT_RE.match(v) or ".." in v:
        raise BadRequest(msg("bad_docroot"))
    return v


# ---------- system status ----------

def _size_to_bytes(s):
    m = re.match(r"^([\d.]+)\s*([kKMGT]?i?B)$", (s or "").strip())
    if not m:
        return 0
    mult = {"B": 1, "kB": 1e3, "KB": 1e3, "KiB": 1024, "MB": 1e6, "MiB": 1024 ** 2, "GB": 1e9,
            "GiB": 1024 ** 3, "TB": 1e12, "TiB": 1024 ** 4}
    return int(float(m.group(1)) * mult.get(m.group(2), 1))


def latest_ddev():
    def fetch():
        try:
            req = urllib.request.Request("https://api.github.com/repos/ddev/ddev/releases/latest",
                                         headers={"User-Agent": "ddev-dashboard",
                                                  "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.load(r)
            return {"version": data.get("tag_name"), "url": data.get("html_url")}
        except Exception:  # noqa: BLE001 - offline is fine
            return None
    return cached("latest_ddev", 6 * 3600, fetch)


def system_info():
    if DEMO:
        return demo_data()["system"]

    def collect():
        info = {"ddev": None, "docker": None, "docker_platform": None, "docker_ok": False,
                "disk": [], "stats": {}, "latest": latest_ddev()}
        try:
            code, out = run(ddev("version", "-j"), timeout=30)
            raw = json_raw(out) or {}
            info.update(ddev=raw.get("DDEV version"), docker=raw.get("docker"),
                        docker_platform=raw.get("docker-platform"))
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            code, out = run(["docker", "system", "df", "--format", "{{json .}}"], timeout=30)
            info["docker_ok"] = code == 0
            for line in out.splitlines():
                if line.startswith("{"):
                    d = json.loads(line)
                    info["disk"].append({"type": d.get("Type"), "size": d.get("Size"),
                                         "reclaimable": d.get("Reclaimable")})
            code, out = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=30)
            for line in out.splitlines():
                if not line.startswith("{"):
                    continue
                s = json.loads(line)
                m = re.match(r"^ddev-(.+)-(web|db|[a-z0-9_]+)$", s.get("Name", ""))
                if not m or s["Name"] in ("ddev-router", "ddev-ssh-agent"):
                    continue
                proj = m.group(1)
                agg = info["stats"].setdefault(proj, {"cpu": 0.0, "mem": 0})
                agg["cpu"] = round(agg["cpu"] + float(s.get("CPUPerc", "0%").rstrip("%") or 0), 2)
                agg["mem"] += _size_to_bytes(s.get("MemUsage", "").split("/")[0])
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return info
    return cached("system", 10, collect)


# ---------- jobs ----------

def start_job(key, action, steps, cleanup=None):
    """steps: (argv, cwd) tuples or callables, run in order; stops at first failure.
    A callable may return a list of further steps, which run right after it.
    cleanup: optional callable that always runs at the end."""
    with lock:
        if key in busy or "*" in busy:
            raise BadRequest(msg("busy"))
        busy[key] = {"action": action, "output": "", "started": time.time()}
    threading.Thread(target=_run_job, args=(key, action, list(steps), cleanup), daemon=True).start()


def _append(key, text):
    with lock:
        job = busy.get(key)
        if job is not None:
            job["output"] = (job["output"] + text)[-20000:]


def _run_job(key, action, steps, cleanup=None):
    ok = True
    try:
        while steps:
            step = steps.pop(0)
            if callable(step):
                more = step()
                if more:
                    steps[:0] = more
                continue
            argv, cwd = step
            _append(key, f"$ {' '.join(argv)}\n")
            p = subprocess.Popen(argv, cwd=cwd or HERE, env=ENV, bufsize=1,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **POPEN_TEXT)
            for line in p.stdout:
                _append(key, line)
            if p.wait() != 0:
                ok = False
                _append(key, f"\n✗ exit code {p.returncode}\n")
                break
    except Exception as e:  # noqa: BLE001
        ok = False
        _append(key, f"\n✗ {e}\n")
    if cleanup:
        try:
            cleanup()
        except Exception:  # noqa: BLE001
            pass
    _cache.pop("system", None)
    with lock:
        job = busy.pop(key, {"output": ""})
        last_log[key] = {"action": action, "ok": ok, "output": job["output"][-8000:],
                         "time": time.time()}


def restart_if_running(p):
    return [(ddev("restart", p["name"]), p["approot"])] if p["status"] == "running" else []


def open_folder(path):
    e = env()
    if e == "wsl":
        win = subprocess.run(["wslpath", "-w", path], capture_output=True, timeout=5, **POPEN_TEXT).stdout.strip()
        argv, cwd = ["/mnt/c/Windows/explorer.exe", win], "/mnt/c"
    elif e == "windows":
        argv, cwd = ["explorer", path], None
    elif e == "macos":
        argv, cwd = ["open", path], None
    else:
        argv, cwd = ["xdg-open", path], None
    subprocess.Popen(argv, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def create_steps(prm):
    name = str(prm.get("name", "")).strip().lower()
    if not NAME_RE.match(name):
        raise BadRequest(msg("bad_name"))
    if any(p["name"] == name for p in list_projects()):
        raise BadRequest(msg("exists"))
    path = to_host_path(str(prm.get("path", "")))
    git_url = str(prm.get("git_url", "")).strip()
    config = [ddev("config", f"--project-name={name}",
                   f"--project-type={pick(prm.get('type'), PROJECT_TYPES)}",
                   f"--docroot={clean_docroot(prm.get('docroot'))}", "--create-docroot",
                   f"--php-version={pick(prm.get('php_version'), PHP_VERSIONS)}",
                   f"--webserver-type={pick(prm.get('webserver_type'), WEBSERVERS)}",
                   f"--database={pick(prm.get('database'), DATABASES)}")]
    hosts = clean_hosts(prm.get("additional_hostnames"), True)
    if hosts:
        config[0].append("--additional-hostnames=" + ",".join(hosts))
    tail = []
    if prm.get("phpmyadmin"):
        tail.append((ddev("add-on", "get", "ddev/ddev-phpmyadmin"), path))
    if prm.get("start"):
        tail.append((ddev("start"), path))

    if git_url:
        if not GIT_URL_RE.match(git_url):
            raise BadRequest(msg("bad_git"))
        if os.path.exists(path) and os.listdir(path):
            raise BadRequest(msg("not_empty"))

        def after_clone():
            if os.path.exists(os.path.join(path, ".ddev", "config.yaml")):
                _append(name, msg("skip_config") + "\n")
                return [s for s in tail if s[0][1] != "add-on" or not os.path.exists(
                    os.path.join(path, ".ddev", "docker-compose.phpmyadmin.yaml"))]
            return [(config[0], path)] + tail
        parent = os.path.dirname(path)
        return name, [lambda: os.makedirs(parent, exist_ok=True),
                      (["git", "clone", "--", git_url, path], parent), after_clone]

    if os.path.exists(os.path.join(path, ".ddev", "config.yaml")):
        raise BadRequest(msg("has_ddev"))
    return name, [lambda: os.makedirs(path, exist_ok=True), (config[0], path)] + tail


def handle_action(body):
    if DEMO:
        raise BadRequest(msg("demo"))
    action = body.get("action")
    name = body.get("name", "")
    prm = body.get("params") or {}

    if action == "poweroff":
        return start_job("*", "poweroff", [(ddev("poweroff"), None)])

    if action == "create":
        key, steps = create_steps(prm)
        return start_job(key, "create", steps)

    p = get_project(name)
    root = p["approot"]
    running = p["status"] == "running"

    def need_running():
        if not running:
            raise BadRequest(msg("not_running"))

    if action in ("start", "stop", "restart"):
        return start_job(name, action, [(ddev(action, name), root)])

    if action == "open":
        return open_folder(root)

    if action == "settings":
        args = ddev("config",
                    f"--project-type={pick(prm.get('type'), PROJECT_TYPES + [p.get('type')])}",
                    f"--docroot={clean_docroot(prm.get('docroot'))}",
                    f"--php-version={pick(prm.get('php_version'), PHP_VERSIONS)}",
                    f"--webserver-type={pick(prm.get('webserver_type'), WEBSERVERS)}")
        return start_job(name, "settings", [(args, root)] + restart_if_running(p))

    if action == "domains":
        hosts = clean_hosts(prm.get("additional_hostnames"), True)
        fqdns = clean_hosts(prm.get("additional_fqdns"), False)
        args = ddev("config", "--additional-hostnames=" + ",".join(hosts),
                    "--additional-fqdns=" + ",".join(fqdns))
        return start_job(name, "domains", [(args, root)] + restart_if_running(p))

    if action == "xdebug":
        need_running()
        return start_job(name, "xdebug", [(ddev("xdebug", "on" if prm.get("on") else "off"), root)])

    if action == "phpmyadmin":
        return start_job(name, "phpmyadmin",
                         [(ddev("add-on", "get", "ddev/ddev-phpmyadmin"), root)] + restart_if_running(p))

    if action == "addon-get":
        repo = str(prm.get("repo", "")).strip()
        if not REPO_RE.match(repo):
            raise BadRequest(msg("bad_repo"))
        return start_job(name, "addon-get", [(ddev("add-on", "get", repo), root)] + restart_if_running(p))

    if action == "addon-remove":
        addon = str(prm.get("addon", ""))
        if addon not in {a["name"] for a in installed_addons(root)}:
            raise BadRequest(msg("unknown_addon"))
        return start_job(name, "addon-remove", [(ddev("add-on", "remove", addon), root)] + restart_if_running(p))

    if action == "exec":
        need_running()
        cid = prm.get("command")
        if cid:
            cfg = read_config(root)
            if cid not in {c["id"] for c in commands_for(cfg.get("type") or p.get("type"), root)}:
                raise BadRequest(msg("bad_command"))
            return start_job(name, "exec", [(ddev(*COMMANDS[cid]["args"]), root)])
        custom = str(prm.get("custom", "")).strip()
        if not custom or len(custom) > 1000:
            raise BadRequest(msg("bad_command"))
        # runs inside the project's web container, not on the host
        return start_job(name, "exec", [(ddev("exec", "bash", "-lc", custom), root)])

    if action == "snapshot":
        need_running()
        label = str(prm.get("label", "")).strip()
        args = ddev("snapshot", name)
        if label:
            if not NAME_RE.match(label.replace("_", "-")):
                raise BadRequest(msg("bad_snapshot_name"))
            args.append(f"--name={label}")
        return start_job(name, "snapshot", [(args, root)])

    if action in ("snapshot-restore", "snapshot-delete"):
        snap = str(prm.get("snapshot", ""))
        if snap not in {s["name"] for s in snapshots(root)}:
            raise BadRequest(msg("unknown_snapshot"))
        if action == "snapshot-restore":
            need_running()
            return start_job(name, action, [(ddev("snapshot", "restore", snap), root)])

        def remove():
            d = os.path.join(root, ".ddev", "db_snapshots")
            for fn in os.listdir(d):
                m = SNAP_RE.match(fn)
                if m and m.group(1) == snap:
                    full = os.path.join(d, fn)
                    shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)
                    _append(name, msg("deleted", v=fn) + "\n")
        return start_job(name, action, [remove])

    if action == "delete":
        args = ddev("delete", "-y", name)
        if prm.get("omit_snapshot"):
            args.insert(2, "-O")
        return start_job(name, "delete", [(args, root)])

    raise BadRequest(msg("unknown_action"))


def save_settings(body):
    new = dict(settings)
    if "lang" in body:
        new["lang"] = pick(body["lang"], LANGS)
    if "env" in body:
        new["env"] = pick(body["env"], ENVIRONMENTS)
    if "projects_dir" in body:
        new["projects_dir"] = str(body["projects_dir"]).strip()[:500]
    settings.update(new)
    if not DEMO:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)


def settings_payload():
    try:
        projects = list_projects()
    except Exception:  # noqa: BLE001
        projects = []
    return {**settings, "detected_env": DETECTED_ENV, "effective_env": env(),
            "default_projects_dir": default_projects_dir(projects),
            "panel_dir": HERE, "python": sys.executable or "python3", "port": PORT,
            "wsl_distro": WSL_DISTRO, "version": __version__, "demo": DEMO}


# ---------- demo data ----------

_demo = None


def demo_data():
    global _demo
    if _demo is None:
        with open(os.path.join(HERE, "tests", "demo.json"), encoding="utf-8") as f:
            _demo = json.load(f)
    return _demo


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _file(self, name, ctype):
        try:
            with open(os.path.join(HERE, name), "rb") as f:
                return self._send(200, f.read(), ctype)
        except OSError:
            return self._send(404, {"error": "not found"})

    def _host_ok(self):
        # Blocks DNS rebinding: only requests addressed to localhost.
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def _post_ok(self):
        # Custom header forces a CORS preflight, so other sites can't trigger actions.
        return self._host_ok() and self.headers.get("X-DDEV-Dashboard") == "1"

    def _json_body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._file("index.html", "text/html; charset=utf-8")
            if re.fullmatch(r"/assets/[a-z0-9-]+\.svg", u.path):
                return self._file(u.path.lstrip("/"), "image/svg+xml")
            if u.path == "/api/projects":
                projects = list_projects()
                with lock:
                    return self._send(200, {"projects": projects, "busy": json.loads(json.dumps(busy)),
                                            "logs": dict(last_log)})
            if u.path == "/api/options":
                return self._send(200, {"php": PHP_VERSIONS, "webservers": WEBSERVERS,
                                        "databases": DATABASES, "types": PROJECT_TYPES, "addons": ADDONS})
            if u.path == "/api/settings":
                return self._send(200, settings_payload())
            if u.path == "/api/system":
                return self._send(200, system_info())
            if u.path == "/api/project":
                return self._send(200, project_details(q.get("name", "")))
            if u.path == "/api/logs":
                svc = pick(q.get("service", "web"), {"web", "db"})
                if DEMO:
                    return self._send(200, {"output": demo_data()["logs"][svc]})
                p = get_project(q.get("name", ""))
                code, out = run(ddev("logs", "-s", svc, "--tail", "300", p["name"]), cwd=p["approot"], timeout=60)
                return self._send(200, {"output": out})
            if u.path == "/api/export":
                return self._export(q.get("name", ""))
        except BadRequest as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def _export(self, name):
        if DEMO:
            raise BadRequest(msg("demo"))
        p = get_project(name)
        if p["status"] != "running":
            raise BadRequest(msg("not_running"))
        fn = f"{name}-{time.strftime('%Y%m%d-%H%M')}.sql.gz"
        proc = subprocess.Popen(ddev("export-db", name, "--gzip"), cwd=p["approot"], env=ENV,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            shutil.copyfileobj(proc.stdout, self.wfile, 1 << 16)
        finally:
            proc.wait()
            self.close_connection = True

    def do_POST(self):
        if not self._post_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        try:
            if u.path == "/api/action":
                handle_action(self._json_body())
                return self._send(202, {"ok": True})
            if u.path == "/api/settings":
                save_settings(self._json_body())
                return self._send(200, settings_payload())
            if u.path == "/api/import":
                return self._import(parse_qs(u.query).get("name", [""])[0])
        except BadRequest as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def _import(self, name):
        if DEMO:
            raise BadRequest(msg("demo"))
        p = get_project(name)
        if p["status"] != "running":
            raise BadRequest(msg("not_running"))
        fname = os.path.basename(self.headers.get("X-Filename", "dump.sql"))
        m = re.search(r"(\.sql(\.(gz|bz2|xz|zst))?|\.tar(\.(gz|bz2|xz|zst))?|\.tgz|\.zip)$", fname.lower())
        if not m:
            raise BadRequest(msg("bad_file"))
        length = int(self.headers.get("Content-Length", 0))
        # no leading dot: ddev skips hidden files when it unpacks the dump
        dest = os.path.join(p["approot"], ".ddev", f"dashboard-import-{int(time.time())}{m.group(1)}")
        with open(dest, "wb") as f:
            left = length
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        try:
            start_job(name, "import-db", [(ddev("import-db", f"--file={dest}"), p["approot"])],
                      cleanup=lambda: os.remove(dest))
        except BadRequest:
            os.remove(dest)
            raise
        self._send(202, {"ok": True})


def main():
    print(f"DDEV Dashboard {__version__} on http://localhost:{PORT}  (environment: {env()}"
          f"{', demo' if DEMO else ''})", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
