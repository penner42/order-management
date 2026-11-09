"""Production serves committed releases without invoking signing or writing files."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.routers import browser_extension
from app.utils import browser_extension as ext


class PackagedExtensionTests(unittest.TestCase):
    def test_production_serves_both_packages_without_keys_or_builds(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = root / "release"
            release.mkdir()
            meta = {"version": "1.2.3", "fingerprint": "built-in-dev", "built_at": "2026-11-09T00:00:00Z",
                    "chrome_filename": "extension.crx", "firefox_filename": "extension.xpi"}
            (release / "release.json").write_text(json.dumps(meta))
            for name in ("extension.crx", "extension.xpi"):
                (release / name).write_bytes(name.encode())
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in release.iterdir()}
            with patch.object(ext.settings, "browser_extension_build_enabled", False), \
                 patch.object(ext.settings, "web_ext_api_key", ""), \
                 patch.object(ext.settings, "web_ext_api_secret", ""), \
                 patch.object(ext, "extension_dir", return_value=root), \
                 patch.object(ext, "_sign_extension") as sign, \
                 patch.object(ext.threading, "Thread") as thread:
                ext.ensure_signed(force=True)
                ext.ensure_signed_async(force=True)
                status = ext.get_status()
                self.assertEqual(status["status"], "ready")
                self.assertFalse(status["build_enabled"])
                self.assertEqual(status["meta"]["fingerprint"], "built-in-dev")
                app = FastAPI()
                app.include_router(browser_extension.router)
                app.dependency_overrides[get_current_user] = lambda: object()
                client = TestClient(app)
                for browser, filename in (("chrome", "extension.crx"), ("firefox", "extension.xpi")):
                    response = client.get(f"/browser-extension/download/{browser}")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, filename.encode())
                self.assertEqual(client.post("/browser-extension/rebuild").status_code, 403)
                sign.assert_not_called()
                thread.assert_not_called()
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in release.iterdir()})

    def test_missing_release_does_not_start_a_build(self):
        with TemporaryDirectory() as tmp, \
             patch.object(ext.settings, "browser_extension_build_enabled", False), \
             patch.object(ext, "extension_dir", return_value=Path(tmp)), \
             patch.object(ext, "ensure_signed_async") as build:
            self.assertEqual(ext.get_status()["status"], "unavailable")
            self.assertIsNone(ext.artifact_path("firefox"))
            build.assert_not_called()

    def test_dev_publishes_current_packages_and_removes_old_release(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dist").mkdir()
            (root / "release").mkdir()
            (root / "release" / "old.xpi").write_bytes(b"old")
            (root / "dist" / "current.crx").write_bytes(b"chrome")
            (root / "dist" / "current.xpi").write_bytes(b"firefox")
            meta = {"chrome_filename": "current.crx", "firefox_filename": "current.xpi"}
            fingerprint = ext.source_fingerprint(root)
            ext._publish_release(root, meta)
            self.assertEqual(ext.source_fingerprint(root), fingerprint)
            self.assertEqual((root / "release" / "current.crx").read_bytes(), b"chrome")
            self.assertEqual((root / "release" / "current.xpi").read_bytes(), b"firefox")
            self.assertFalse((root / "release" / "old.xpi").exists())
            self.assertEqual(json.loads((root / "release" / "release.json").read_text()), meta)
