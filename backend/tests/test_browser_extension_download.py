"""Extension downloads must not reuse a cached release under a new filename."""
import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.routers import browser_extension


class BrowserExtensionDownloadTests(unittest.TestCase):
    def test_download_serves_requested_release_without_caching(self):
        app = FastAPI()
        app.include_router(browser_extension.router)
        app.dependency_overrides[get_current_user] = lambda: object()
        with TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "order_manager_browser_integration-0.1.39.xpi"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"version": "0.1.39"}))
            with patch.object(browser_extension.ext, "artifact_path", return_value=artifact):
                client = TestClient(app)
                response = client.get("/browser-extension/download/firefox", params={
                    "filename": artifact.name, "build": "current-source-fingerprint",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertIn(artifact.name, response.headers["content-disposition"])
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    self.assertEqual(json.loads(archive.read("manifest.json"))["version"], "0.1.39")
                stale = client.get("/browser-extension/download/firefox", params={
                    "filename": "order_manager_browser_integration-0.1.37.xpi",
                })
                self.assertEqual(stale.status_code, 409)

    def test_build_status_is_not_cached(self):
        app = FastAPI()
        app.include_router(browser_extension.router)
        app.dependency_overrides[get_current_user] = lambda: object()
        with patch.object(browser_extension.ext, "get_status", return_value={
            "status": "ready", "error": None, "available": True, "meta": None,
        }):
            response = TestClient(app).get("/browser-extension")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
