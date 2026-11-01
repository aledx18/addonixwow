"""End-to-end tests: run the CLI against a local fake of the CurseForge API."""

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CLI = Path(__file__).resolve().parent.parent / "backend" / "addonix.py"
KEY = "$2a$10$secret-test-key"
FOREVER_TYPE = 99


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_symlink_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Evil/Evil.toc", "## Title: Evil\n")
        info = zipfile.ZipInfo("Evil/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


class FakeCurseForge(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        state = self.server.fake
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        state["requests"].append(
            {"path": parsed.path, "query": query, "key": self.headers.get("x-api-key"), "host": self.headers.get("Host")}
        )
        if parsed.path.startswith("/cdn/"):
            body = state["blobs"][parsed.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.headers.get("x-api-key") != KEY:
            self.send_json({}, 403)
            return
        if parsed.path == "/v1/games/1/version-types":
            self.send_json({"data": state["version_types"]})
        elif parsed.path == "/v1/mods/search" and state.get("truncate_search"):
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b'{"data"')
        elif parsed.path == "/v1/mods/search":
            self.send_json(
                {
                    "data": [
                        {
                            "id": 7,
                            "name": "Deadly Boss Mods",
                            "summary": "Boss timers",
                            "downloadCount": 1234,
                            "logo": {"thumbnailUrl": "https://img/logo.png"},
                            "links": {"websiteUrl": "https://curseforge.com/wow/addons/dbm"},
                        }
                    ],
                    "pagination": {"totalCount": 1},
                }
            )
        elif parsed.path == "/v1/mods/7":
            self.send_json({"data": {"id": 7, "name": "Deadly Boss Mods", "links": {"websiteUrl": "https://curseforge.com/wow/addons/dbm"}}})
        elif parsed.path == "/v1/mods/7/files":
            self.send_json({"data": state["files"]})
        elif parsed.path.endswith("/download-url"):
            self.send_json({"data": state["download_url"]})
        else:
            self.send_json({}, 404)


class AddonixTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCurseForge)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.server.fake = {
            "requests": [],
            "blobs": {},
            "files": [],
            "download_url": None,
            "version_types": [{"id": 1, "name": "Retail", "slug": "wow-retail"}, {"id": FOREVER_TYPE, "name": "WoW Forever", "slug": "wow-forever"}],
        }
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addons = self.home / "Faugus/battlenet/drive_c/Program Files (x86)/World of Warcraft/_forever_/Interface/AddOns"
        self.addons.mkdir(parents=True)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def run_cli(self, *args, key=KEY, stdin=None):
        env = {
            "HOME": str(self.home),
            "PATH": os.environ["PATH"],
            "ADDONIX_CF_BASE": self.base,
        }
        if key:
            env["CURSEFORGE_API_KEY"] = key
        result = subprocess.run([sys.executable, "-I", str(CLI), *args], env=env, input=stdin, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.stderr, "")
        self.assertNotIn(KEY, result.stdout)
        return result.returncode, json.loads(result.stdout)

    def add_file(self, content, file_id=70, release=1, date="2026-10-01T00:00:00Z", host=None, sha1=None):
        path = f"/cdn/{file_id}.zip"
        self.server.fake["blobs"][path] = content
        entry = {
            "id": file_id,
            "displayName": f"dbm-{file_id}",
            "releaseType": release,
            "fileDate": date,
            "fileLength": len(content),
            "downloadUrl": f"http://{host or '127.0.0.1'}:{self.port}{path}",
            "hashes": [{"algo": 1, "value": sha1 or hashlib.sha1(content).hexdigest()}],
        }
        self.server.fake["files"].append(entry)
        return entry

    def good_zip(self, marker="new"):
        return make_zip({"DBM-Core/DBM-Core.toc": "## Title: DBM Core\n## Version: 1.0\n", "DBM-Core/core.lua": marker, "DBM-GUI/DBM-GUI.toc": "## Title: DBM GUI\n"})

    def test_status_detects_faugus_install_and_missing_key(self):
        code, out = self.run_cli("status", key=None)
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["addonsPath"], str(self.addons))
        self.assertEqual(out["source"], "faugus")
        self.assertFalse(out["hasKey"])
        self.assertEqual(out["installed"], [])

    def test_status_reads_faugus_custom_prefix(self):
        prefix = self.home / "Games/wow/drive_c/Program Files (x86)/World of Warcraft/_forever_/Interface/AddOns"
        prefix.mkdir(parents=True)
        config = self.home / ".config/faugus-launcher"
        config.mkdir(parents=True)
        (config / "config.json").write_text(json.dumps({"default-prefix": str(self.home / "Games")}))
        _, out = self.run_cli("status")
        self.assertEqual(out["addonsPath"], str(prefix))

    def test_status_uses_configured_path_over_detection(self):
        custom = self.home / "custom/AddOns"
        custom.mkdir(parents=True)
        config = self.home / ".config/addonixwow"
        config.mkdir(parents=True)
        (config / "config.json").write_text(json.dumps({"addonsPath": str(custom)}))
        _, out = self.run_cli("status")
        self.assertEqual(out["addonsPath"], str(custom))
        self.assertEqual(out["source"], "config")

    def test_status_without_install_reports_not_found(self):
        import shutil

        shutil.rmtree(self.home / "Faugus")
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(out["addonsPath"], "")
        self.assertEqual(out["source"], "none")

    def test_status_lists_toc_metadata_without_color_codes(self):
        folder = self.addons / "Foo"
        folder.mkdir()
        (folder / "Foo.toc").write_text("## Title: |cff00ff00Foo|r Addon\n## Version: 2.5\n", encoding="utf-8")
        (self.addons / "NotAnAddon").mkdir()
        _, out = self.run_cli("status")
        self.assertEqual(out["installed"], [{"name": "Foo Addon", "version": "2.5", "folders": ["Foo"], "modId": None}])

    def test_search_requires_key(self):
        code, out = self.run_cli("search", "dbm", key=None)
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "missing_key")

    def test_search_filters_by_forever_and_sends_key(self):
        code, out = self.run_cli("search", "dbm")
        self.assertEqual(code, 0)
        self.assertEqual(
            out["results"],
            [
                {
                    "id": 7,
                    "name": "Deadly Boss Mods",
                    "summary": "Boss timers",
                    "downloads": 1234,
                    "logo": "https://img/logo.png",
                    "url": "https://curseforge.com/wow/addons/dbm",
                    "installed": False,
                }
            ],
        )
        search = [r for r in self.server.fake["requests"] if r["path"] == "/v1/mods/search"][0]
        self.assertEqual(search["query"]["gameVersionTypeId"], [str(FOREVER_TYPE)])
        self.assertEqual(search["query"]["searchFilter"], ["dbm"])
        self.assertEqual(search["query"]["gameId"], ["1"])

    def test_search_fails_closed_when_forever_is_not_listed(self):
        self.server.fake["version_types"] = [{"id": 1, "name": "Retail", "slug": "wow-retail"}]
        code, out = self.run_cli("search", "dbm")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "flavor_unavailable")

    def test_invalid_key_is_reported(self):
        code, out = self.run_cli("search", "dbm", key="wrong")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "invalid_key")

    def test_network_failure_is_reported(self):
        self.base = "http://127.0.0.1:9"
        code, out = self.run_cli("search", "dbm")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "network")

    def test_install_extracts_newest_release_and_marks_installed(self):
        self.add_file(self.good_zip("beta"), file_id=71, release=2, date="2026-10-20T00:00:00Z")
        self.add_file(self.good_zip("old"), file_id=69, date="2026-09-01T00:00:00Z")
        self.add_file(self.good_zip("new"), file_id=70)
        code, out = self.run_cli("install", "7")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["folders"], ["DBM-Core", "DBM-GUI"])
        self.assertEqual((self.addons / "DBM-Core/core.lua").read_text(), "new")
        files = [r for r in self.server.fake["requests"] if r["path"] == "/v1/mods/7/files"][0]
        self.assertEqual(files["query"]["gameVersionTypeId"], [str(FOREVER_TYPE)])
        _, search = self.run_cli("search", "dbm")
        self.assertTrue(search["results"][0]["installed"])
        _, status = self.run_cli("status")
        self.assertEqual(status["installed"], [{"name": "Deadly Boss Mods", "version": "dbm-70", "folders": ["DBM-Core", "DBM-GUI"], "modId": 7}])
        self.assertEqual(sorted(p.name for p in self.addons.parent.iterdir()), ["AddOns"])

    def test_install_falls_back_to_beta_when_no_release_exists(self):
        self.add_file(self.good_zip("beta"), file_id=71, release=2)
        code, _ = self.run_cli("install", "7")
        self.assertEqual(code, 0)
        self.assertEqual((self.addons / "DBM-Core/core.lua").read_text(), "beta")

    def test_reinstall_replaces_folder_contents(self):
        old = self.addons / "DBM-Core"
        old.mkdir()
        (old / "stale.lua").write_text("stale")
        self.add_file(self.good_zip())
        code, _ = self.run_cli("install", "7")
        self.assertEqual(code, 0)
        self.assertFalse((old / "stale.lua").exists())
        self.assertTrue((old / "core.lua").exists())

    def test_install_without_compatible_file(self):
        code, out = self.run_cli("install", "7")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "no_compatible_file")

    def test_install_uses_download_url_endpoint_when_url_is_null(self):
        entry = self.add_file(self.good_zip())
        self.server.fake["download_url"] = entry["downloadUrl"]
        entry["downloadUrl"] = None
        code, _ = self.run_cli("install", "7")
        self.assertEqual(code, 0)

    def test_install_reports_author_blocked_distribution_with_link(self):
        entry = self.add_file(self.good_zip())
        entry["downloadUrl"] = None
        code, out = self.run_cli("install", "7")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "distribution_disabled")
        self.assertEqual(out["url"], "https://curseforge.com/wow/addons/dbm")

    def assert_rejected(self, content, code_name="invalid_archive"):
        existing = self.addons / "DBM-Core"
        existing.mkdir(exist_ok=True)
        (existing / "keep.lua").write_text("keep")
        self.add_file(content)
        code, out = self.run_cli("install", "7")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], code_name)
        self.assertEqual((existing / "keep.lua").read_text(), "keep")
        self.assertEqual(sorted(p.name for p in self.addons.parent.iterdir()), ["AddOns"])
        self.assertEqual(sorted(p.name for p in self.addons.iterdir()), ["DBM-Core"])

    def test_install_rejects_path_traversal(self):
        self.assert_rejected(make_zip({"DBM-Core/DBM-Core.toc": "x", "../../../evil.lua": "x"}))
        self.assertFalse((self.addons.parents[2] / "evil.lua").exists())

    def test_install_rejects_absolute_paths(self):
        self.assert_rejected(make_zip({"DBM-Core/DBM-Core.toc": "x", "/tmp/evil.lua": "x"}))

    def test_install_rejects_symlinks(self):
        self.assert_rejected(make_symlink_zip())

    def test_install_rejects_files_at_archive_root(self):
        self.assert_rejected(make_zip({"DBM-Core/DBM-Core.toc": "x", "readme.txt": "x"}))

    def test_install_rejects_archive_without_toc(self):
        self.assert_rejected(make_zip({"DBM-Core/core.lua": "x"}))

    def test_install_rejects_corrupt_zip(self):
        self.assert_rejected(b"this is not a zip")

    def test_install_rejects_dot_prefixed_entries_that_target_addons_itself(self):
        self.assert_rejected(make_zip({"./Evil.toc": "x", "./evil.lua": "x"}))

    def test_install_rejects_nested_dot_folder_archive(self):
        self.assert_rejected(make_zip({"./Foo/Foo.toc": "x", "./Bar.toc": "x"}))

    def test_install_reports_encrypted_archive_as_invalid(self):
        content = bytearray(make_zip({"DBM-Core/DBM-Core.toc": "x"}))
        content[content.find(b"PK\x03\x04") + 6] = 1
        content[content.find(b"PK\x01\x02") + 8] = 1
        self.assert_rejected(bytes(content))

    def test_dropped_api_response_is_reported_as_network_error(self):
        self.server.fake["truncate_search"] = True
        code, out = self.run_cli("search", "dbm")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "network")

    def test_malformed_state_still_prints_json_error(self):
        state = self.home / ".local/state/addonixwow"
        state.mkdir(parents=True)
        (state / "state.json").write_text(json.dumps({"mods": {"1": "x"}}))
        code, out = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "internal")

    def test_install_rejects_hash_mismatch(self):
        existing = self.addons / "DBM-Core"
        existing.mkdir()
        (existing / "keep.lua").write_text("keep")
        self.add_file(self.good_zip(), sha1="0" * 40)
        code, out = self.run_cli("install", "7")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "integrity")
        self.assertEqual((existing / "keep.lua").read_text(), "keep")

    def test_download_does_not_send_key_to_untrusted_host(self):
        self.add_file(self.good_zip(), host="localhost")
        code, _ = self.run_cli("install", "7")
        self.assertEqual(code, 0)
        download = [r for r in self.server.fake["requests"] if r["path"].startswith("/cdn/")][0]
        self.assertIsNone(download["key"])

    def test_install_rejects_insecure_download_url(self):
        entry = self.add_file(self.good_zip())
        entry["downloadUrl"] = "http://example.invalid/file.zip"
        code, out = self.run_cli("install", "7")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "network")

    def test_set_key_validates_and_stores_with_private_mode(self):
        code, out = self.run_cli("set-key", key=None, stdin=KEY + "\n")
        self.assertEqual(code, 0, out)
        path = self.home / ".config/addonixwow/config.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())["apiKey"], KEY)
        _, status = self.run_cli("status", key=None)
        self.assertTrue(status["hasKey"])

    def test_set_key_rejects_invalid_key_without_storing(self):
        code, out = self.run_cli("set-key", key=None, stdin="wrong\n")
        self.assertEqual(code, 1)
        self.assertEqual(out["code"], "invalid_key")
        self.assertFalse((self.home / ".config/addonixwow/config.json").exists())


if __name__ == "__main__":
    unittest.main()
