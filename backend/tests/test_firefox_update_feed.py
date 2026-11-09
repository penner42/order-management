"""Public Firefox updates reference the matching signed artifact, never credentials."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from app.utils import browser_extension as ext


class FirefoxUpdateFeedTests(unittest.TestCase):
    def fixture(self, root, *, signed=True, package_version="1.2.3", update_url=True):
        release = root / "release"
        release.mkdir()
        manifest = {"version": "1.2.3", "browser_specific_settings": {"gecko": {
            "id": "order-manager@order-management.local", "strict_min_version": "109.0",
            "update_url": "https://example.org/firefox-updates.json"}}}
        (root / "manifest.json").write_text(json.dumps(manifest))
        packaged = json.loads(json.dumps(manifest))
        packaged["version"] = package_version
        if not update_url:
            del packaged["browser_specific_settings"]["gecko"]["update_url"]
        artifact = release / "extension-1.2.3.xpi"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("manifest.json", json.dumps(packaged))
            if signed:
                archive.writestr("META-INF/mozilla.rsa", "test signature marker")
        return release, artifact, {"version": "1.2.3", "firefox_filename": artifact.name}

    def test_feed_matches_signed_package_version_and_hash(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            release, artifact, meta = self.fixture(root)
            ext._publish_firefox_updates(root, release, meta)
            feed = json.loads((release / "firefox-updates.json").read_text())
            update = feed["addons"]["order-manager@order-management.local"]["updates"][0]
            self.assertEqual(update["version"], "1.2.3")
            self.assertEqual(update["update_hash"], "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest())
            self.assertTrue(update["update_link"].endswith("/master/browser-extension/release/extension-1.2.3.xpi"))

    def test_unsigned_mismatched_or_old_packages_cannot_publish(self):
        for kwargs in ({"signed": False}, {"package_version": "1.2.2"}, {"update_url": False}):
            with self.subTest(kwargs=kwargs), TemporaryDirectory() as tmp:
                root = Path(tmp)
                release, _, meta = self.fixture(root, **kwargs)
                previous = release / "firefox-updates.json"
                previous.write_text("previous valid feed")
                with self.assertRaises(ValueError):
                    ext._publish_firefox_updates(root, release, meta)
                self.assertEqual(previous.read_text(), "previous valid feed")
