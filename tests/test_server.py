"""Tests for DDEV Dashboard. They need no ddev or Docker: the HTTP tests use demo mode.

Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

PORT = 18099
os.environ["DDEV_DASHBOARD_DEMO"] = "1"
os.environ["DDEV_DASHBOARD_PORT"] = str(PORT)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402

CONFIG = """\
name: shop
type: laravel
docroot: public
php_version: "8.3"
webserver_type: nginx-fpm
xdebug_enabled: false
additional_hostnames:
    - admin
    - api
additional_fqdns: []
database:
    type: mariadb
    version: "11.8"
# a comment
use_dns_when_possible: true
"""


class WithEnv:
    """Temporarily set the dashboard's environment setting."""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.old = server.settings["env"]
        server.settings["env"] = self.value

    def __exit__(self, *exc):
        server.settings["env"] = self.old


class YamlTests(unittest.TestCase):
    def test_simple_yaml_reads_ddev_config(self):
        d = server.simple_yaml(CONFIG)
        self.assertEqual(d["type"], "laravel")
        self.assertEqual(d["php_version"], "8.3")
        self.assertEqual(d["additional_hostnames"], ["admin", "api"])
        self.assertEqual(d["additional_fqdns"], [])
        self.assertEqual(d["database"], {"type": "mariadb", "version": "11.8"})
        self.assertIs(d["xdebug_enabled"], False)

    def test_inline_list(self):
        self.assertEqual(server.simple_yaml("additional_fqdns: [a.test, b.test]")["additional_fqdns"],
                         ["a.test", "b.test"])


class PathTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX paths")
    def test_wsl_converts_windows_paths(self):
        with WithEnv("wsl"):
            self.assertEqual(server.to_host_path(r"D:\My Projects\site"), "/mnt/d/My Projects/site")
            self.assertEqual(server.to_host_path("/home/me/site/"), "/home/me/site")

    @unittest.skipIf(os.name == "nt", "POSIX paths")
    def test_linux_needs_absolute_path(self):
        with WithEnv("linux"):
            self.assertEqual(server.to_host_path("/srv/www/site"), "/srv/www/site")
            with self.assertRaises(server.BadRequest):
                server.to_host_path("relative/site")
            with self.assertRaises(server.BadRequest):
                server.to_host_path(r"D:\site")

    def test_windows_needs_drive(self):
        with WithEnv("windows"):
            self.assertTrue(server.to_host_path(r"D:\Projects\site").startswith("D:"))
            with self.assertRaises(server.BadRequest):
                server.to_host_path("Projects/site")


class ValidationTests(unittest.TestCase):
    def test_hosts(self):
        self.assertEqual(server.clean_hosts(["Admin", "api.ddev.site", "", "admin"], True), ["admin", "api"])
        self.assertEqual(server.clean_hosts(["*.shop"], True), ["*.shop"])
        self.assertEqual(server.clean_hosts(["mysite.test"], False), ["mysite.test"])
        for bad in ["bad domain", "-x", "a..b", "x;rm -rf /", "a_b"]:
            with self.assertRaises(server.BadRequest, msg=bad):
                server.clean_hosts([bad], False)

    def test_docroot(self):
        self.assertEqual(server.clean_docroot("/public/"), "public")
        self.assertEqual(server.clean_docroot("web\\app"), "web/app")
        self.assertEqual(server.clean_docroot(""), "")
        for bad in ["../etc", "a/../../b", "x y", "a;b"]:
            with self.assertRaises(server.BadRequest, msg=bad):
                server.clean_docroot(bad)

    def test_regexes(self):
        self.assertTrue(server.NAME_RE.match("my-site2"))
        self.assertFalse(server.NAME_RE.match("-site"))
        self.assertFalse(server.NAME_RE.match("my site"))
        self.assertTrue(server.REPO_RE.match("ddev/ddev-redis"))
        self.assertFalse(server.REPO_RE.match("ddev/ddev-redis; rm"))
        self.assertTrue(server.GIT_URL_RE.match("https://github.com/a/b.git"))
        self.assertTrue(server.GIT_URL_RE.match("git@github.com:a/b.git"))
        self.assertFalse(server.GIT_URL_RE.match("--upload-pack=evil"))
        self.assertFalse(server.GIT_URL_RE.match("file:///etc"))

    def test_pick(self):
        self.assertEqual(server.pick("8.3", server.PHP_VERSIONS), "8.3")
        with self.assertRaises(server.BadRequest):
            server.pick("9.9", server.PHP_VERSIONS)

    def test_snapshot_names(self):
        m = server.SNAP_RE.match("before-upgrade-mariadb_11.8.gz")
        self.assertEqual(m.group(1), "before-upgrade")

    def test_size_to_bytes(self):
        self.assertEqual(server._size_to_bytes("124.2MiB"), int(124.2 * 1024 ** 2))
        self.assertEqual(server._size_to_bytes("1.5GB"), 1_500_000_000)
        self.assertEqual(server._size_to_bytes("junk"), 0)


class CommandTests(unittest.TestCase):
    def test_commands_follow_type_and_files(self):
        with tempfile.TemporaryDirectory() as d:
            ids = {c["id"] for c in server.commands_for("laravel", d)}
            self.assertIn("artisan-migrate", ids)
            self.assertNotIn("composer-install", ids)
            self.assertNotIn("wp-cache-flush", ids)
            open(os.path.join(d, "composer.json"), "w").close()
            ids = {c["id"] for c in server.commands_for("wordpress", d)}
            self.assertIn("composer-install", ids)
            self.assertIn("wp-cache-flush", ids)
            self.assertNotIn("artisan-migrate", ids)


class MessageTests(unittest.TestCase):
    def test_both_languages(self):
        for key, (el, en) in server.MESSAGES.items():
            self.assertTrue(el and en, key)
        old = server.settings["lang"]
        try:
            server.settings["lang"] = "en"
            self.assertEqual(server.msg("not_running"), "The project must be running")
        finally:
            server.settings["lang"] = old


class HttpTests(unittest.TestCase):
    """End-to-end against the real HTTP handler in demo mode."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, path, method="GET", body=None, headers=None, host=f"localhost:{PORT}"):
        h = {"Host": host, **(headers or {})}
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_page_and_assets(self):
        code, body = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn(b"DDEV Dashboard", body)
        self.assertEqual(self.req("/assets/icon.svg")[0], 200)
        self.assertEqual(self.req("/assets/../server.py")[0], 404)
        self.assertEqual(self.req("/assets/missing.svg")[0], 404)

    def test_installable_app(self):
        code, body = self.req("/manifest.webmanifest")
        self.assertEqual(code, 200)
        manifest = json.loads(body)
        self.assertEqual(manifest["display"], "standalone")
        for icon in manifest["icons"]:
            self.assertEqual(self.req(icon["src"])[0], 200, icon["src"])

    def test_projects(self):
        code, body = self.req("/api/projects")
        self.assertEqual(code, 200)
        names = [p["name"] for p in json.loads(body)["projects"]]
        self.assertEqual(names, ["acme-shop", "company-blog", "booking-api", "old-portal"])

    def test_project_details_and_system(self):
        code, body = self.req("/api/project?name=acme-shop")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["additional_hostnames"], ["admin", "api"])
        self.assertEqual(self.req("/api/project?name=nope")[0], 400)
        code, body = self.req("/api/system")
        self.assertEqual(code, 200)
        self.assertIn("stats", json.loads(body))

    def test_settings_and_options(self):
        code, body = self.req("/api/settings")
        self.assertEqual(code, 200)
        s = json.loads(body)
        self.assertTrue(s["demo"])
        self.assertEqual(s["version"], server.__version__)
        code, body = self.req("/api/options")
        self.assertIn("ddev/ddev-redis", json.loads(body)["addons"])

    def test_rejects_foreign_host(self):
        self.assertEqual(self.req("/api/projects", host="evil.example:80")[0], 403)

    def test_actions_need_custom_header(self):
        body = {"name": "acme-shop", "action": "stop"}
        self.assertEqual(self.req("/api/action", "POST", body)[0], 403)
        code, resp = self.req("/api/action", "POST", body, {"X-DDEV-Dashboard": "1"})
        self.assertEqual(code, 400)  # demo mode refuses actions
        self.assertIn("error", json.loads(resp))


if __name__ == "__main__":
    unittest.main()
