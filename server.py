#!/usr/bin/env python3
"""DDEV Dashboard: a local web UI for ddev (projects, domains, settings, database, logs).

Runs anywhere ddev runs (WSL, Linux, macOS, Windows) with plain Python 3.8+, no extra packages.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import yaml  # optional; a small built-in parser is used when missing
except ImportError:
    yaml = None

HOST = "127.0.0.1"
PORT = int(os.environ.get("DDEV_DASHBOARD_PORT", "8099"))
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.json")
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
ENV = {**os.environ, "DDEV_NO_TUI": "true", "DDEV_NONINTERACTIVE": "true"}
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
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$")
HOST_RE = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
DOCROOT_RE = re.compile(r"^[A-Za-z0-9_./-]*$")
SNAP_RE = re.compile(r"^(.+?)-(mysql|mariadb|postgres)_[\d.]+(\.gz|\.zst)?$")

MESSAGES = {
    "busy": ("Τρέχει ήδη εργασία γι' αυτό το project", "A task is already running for this project"),
    "unknown_project": ("Άγνωστο project", "Unknown project"),
    "bad_name": ("Μη έγκυρο όνομα (γράμματα, αριθμοί, παύλες)", "Invalid name (letters, numbers, dashes)"),
    "exists": ("Υπάρχει ήδη project με αυτό το όνομα", "A project with this name already exists"),
    "has_ddev": ("Ο φάκελος έχει ήδη ρύθμιση DDEV", "This folder already has a DDEV config"),
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
}

lock = threading.Lock()
busy = {}      # key -> {"action", "output", "started"}
last_log = {}  # key -> {"action", "ok", "output", "time"}


class BadRequest(Exception):
    pass


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
    data, key, sub = {}, None, None
    for raw in text.splitlines():
        line = raw.split(" #")[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if indent == 0 and ":" in s:
            key, _, val = s.partition(":")
            key, sub = key.strip(), None
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

def run(args, cwd=None, timeout=120):
    p = subprocess.run(["ddev", *args], capture_output=True, timeout=timeout, cwd=cwd or HERE,
                       env=ENV, **POPEN_TEXT)
    return p.returncode, p.stdout + p.stderr


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
    code, out = run(["list", "--json-output"], timeout=60)
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


def as_list(v):
    return [str(x) for x in v] if isinstance(v, list) else []


def project_details(name):
    p = get_project(name)
    cfg = read_config(p["approot"])
    db = cfg.get("database") if isinstance(cfg.get("database"), dict) else {}
    info = {
        "name": name,
        "approot": p["approot"],
        "status": p["status"],
        "type": cfg.get("type") or p.get("type"),
        "docroot": cfg.get("docroot") or "",
        "php_version": str(cfg.get("php_version") or ""),
        "webserver_type": cfg.get("webserver_type") or "",
        "database": f"{db.get('type', '?')}:{db.get('version', '?')}",
        "additional_hostnames": as_list(cfg.get("additional_hostnames")),
        "additional_fqdns": as_list(cfg.get("additional_fqdns")),
        "phpmyadmin": p["phpmyadmin_url"] is not None,
        "snapshots": snapshots(p["approot"]),
        "xdebug": None,
    }
    if p["status"] == "running":
        code, out = run(["xdebug", "status"], cwd=p["approot"], timeout=30)
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


# ---------- jobs ----------

def start_job(key, action, steps, cleanup=None):
    """steps: (args, cwd) tuples or callables, run in order; stops at first failure.
    cleanup: optional callable that always runs at the end."""
    with lock:
        if key in busy or "*" in busy:
            raise BadRequest(msg("busy"))
        busy[key] = {"action": action, "output": "", "started": time.time()}
    threading.Thread(target=_run_job, args=(key, action, steps, cleanup), daemon=True).start()


def _append(key, text):
    with lock:
        job = busy.get(key)
        if job is not None:
            job["output"] = (job["output"] + text)[-20000:]


def _run_job(key, action, steps, cleanup=None):
    ok = True
    try:
        for step in steps:
            if callable(step):
                step()
                continue
            args, cwd = step
            _append(key, f"$ ddev {' '.join(args)}\n")
            p = subprocess.Popen(["ddev", *args], cwd=cwd or HERE, env=ENV, bufsize=1,
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
    with lock:
        job = busy.pop(key, {"output": ""})
        last_log[key] = {"action": action, "ok": ok, "output": job["output"][-8000:],
                         "time": time.time()}


def restart_if_running(p):
    return [(["restart", p["name"]], p["approot"])] if p["status"] == "running" else []


def handle_action(body):
    action = body.get("action")
    name = body.get("name", "")
    prm = body.get("params") or {}

    if action == "poweroff":
        return start_job("*", "poweroff", [(["poweroff"], None)])

    if action == "create":
        name = str(prm.get("name", "")).strip().lower()
        if not NAME_RE.match(name):
            raise BadRequest(msg("bad_name"))
        if any(p["name"] == name for p in list_projects()):
            raise BadRequest(msg("exists"))
        path = to_host_path(str(prm.get("path", "")))
        if os.path.exists(os.path.join(path, ".ddev", "config.yaml")):
            raise BadRequest(msg("has_ddev"))
        args = ["config", f"--project-name={name}",
                f"--project-type={pick(prm.get('type'), PROJECT_TYPES)}",
                f"--docroot={clean_docroot(prm.get('docroot'))}", "--create-docroot",
                f"--php-version={pick(prm.get('php_version'), PHP_VERSIONS)}",
                f"--webserver-type={pick(prm.get('webserver_type'), WEBSERVERS)}",
                f"--database={pick(prm.get('database'), DATABASES)}"]
        hosts = clean_hosts(prm.get("additional_hostnames"), True)
        if hosts:
            args.append("--additional-hostnames=" + ",".join(hosts))
        steps = [lambda: os.makedirs(path, exist_ok=True), (args, path)]
        if prm.get("phpmyadmin"):
            steps.append((["add-on", "get", "ddev/ddev-phpmyadmin"], path))
        if prm.get("start"):
            steps.append((["start", name], path))
        return start_job(name, "create", steps)

    p = get_project(name)
    root = p["approot"]
    running = p["status"] == "running"

    if action in ("start", "stop", "restart"):
        return start_job(name, action, [([action, name], root)])

    if action == "settings":
        args = ["config",
                f"--project-type={pick(prm.get('type'), PROJECT_TYPES + [p.get('type')])}",
                f"--docroot={clean_docroot(prm.get('docroot'))}",
                f"--php-version={pick(prm.get('php_version'), PHP_VERSIONS)}",
                f"--webserver-type={pick(prm.get('webserver_type'), WEBSERVERS)}"]
        return start_job(name, "settings", [(args, root)] + restart_if_running(p))

    if action == "domains":
        hosts = clean_hosts(prm.get("additional_hostnames"), True)
        fqdns = clean_hosts(prm.get("additional_fqdns"), False)
        args = ["config", "--additional-hostnames=" + ",".join(hosts),
                "--additional-fqdns=" + ",".join(fqdns)]
        return start_job(name, "domains", [(args, root)] + restart_if_running(p))

    if action == "xdebug":
        if not running:
            raise BadRequest(msg("not_running"))
        return start_job(name, "xdebug", [(["xdebug", "on" if prm.get("on") else "off"], root)])

    if action == "phpmyadmin":
        return start_job(name, "phpmyadmin",
                         [(["add-on", "get", "ddev/ddev-phpmyadmin"], root)] + restart_if_running(p))

    if action == "snapshot":
        if not running:
            raise BadRequest(msg("not_running"))
        label = str(prm.get("label", "")).strip()
        args = ["snapshot", name]
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
            if not running:
                raise BadRequest(msg("not_running"))
            return start_job(name, action, [(["snapshot", "restore", snap], root)])

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
        args = ["delete", "-y", name]
        if prm.get("omit_snapshot"):
            args.insert(1, "-O")
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
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def settings_payload():
    try:
        projects = list_projects()
    except Exception:  # noqa: BLE001
        projects = []
    return {**settings, "detected_env": DETECTED_ENV, "effective_env": env(),
            "default_projects_dir": default_projects_dir(projects),
            "panel_dir": HERE, "python": sys.executable or "python3",
            "port": PORT}


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
        with open(os.path.join(HERE, name), "rb") as f:
            return self._send(200, f.read(), ctype)

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
                                        "databases": DATABASES, "types": PROJECT_TYPES})
            if u.path == "/api/settings":
                return self._send(200, settings_payload())
            if u.path == "/api/project":
                return self._send(200, project_details(q.get("name", "")))
            if u.path == "/api/logs":
                p = get_project(q.get("name", ""))
                svc = pick(q.get("service", "web"), {"web", "db"})
                code, out = run(["logs", "-s", svc, "--tail", "300", p["name"]], cwd=p["approot"], timeout=60)
                return self._send(200, {"output": out})
            if u.path == "/api/export":
                return self._export(q.get("name", ""))
        except BadRequest as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def _export(self, name):
        p = get_project(name)
        if p["status"] != "running":
            raise BadRequest(msg("not_running"))
        fn = f"{name}-{time.strftime('%Y%m%d-%H%M')}.sql.gz"
        proc = subprocess.Popen(["ddev", "export-db", name, "--gzip"], cwd=p["approot"], env=ENV,
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
            start_job(name, "import-db", [(["import-db", f"--file={dest}"], p["approot"])],
                      cleanup=lambda: os.remove(dest))
        except BadRequest:
            os.remove(dest)
            raise
        self._send(202, {"ok": True})


if __name__ == "__main__":
    print(f"DDEV Dashboard on http://localhost:{PORT}  (environment: {env()})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
