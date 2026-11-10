"""Exercise release metadata generation without Apple tools or application state."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

source = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    shutil.copy(source / "release.py", root)
    shutil.copy(source / "release-settings.json", root)
    bundle = root / "Order Manager.app"
    (bundle / "Contents/Resources").mkdir(parents=True)
    environment = dict(os.environ, SOURCE_COMMIT="a" * 40, RELEASE_TAG="mac-installer-17-2")
    def run(command, **changes):
        return subprocess.run([sys.executable, str(root / "release.py"), command, str(bundle)],
                              env=dict(environment, **changes), capture_output=True)
    assert run("bootstrap").returncode == 0
    info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    bootstrap = json.loads((bundle / "Contents/Resources/bootstrap.json").read_text())
    assert info["CFBundleIdentifier"] == "com.penner42.OrderManager"
    assert info["LSMinimumSystemVersion"] == bootstrap["minimumMacOS"] == "15.0"
    assert bootstrap["architecture"] == "arm64" and bootstrap["helperProtocolVersion"] == 1
    output = root / "build/output"
    output.mkdir(parents=True)
    (output / "OrderManager-arm64.zip").write_bytes(b"launcher archive fixture")
    assert run("manifest").returncode == 0
    manifest = json.loads((output / "mac-release.json").read_text())
    assert manifest["launcherSha256"] == hashlib.sha256(b"launcher archive fixture").hexdigest()
    assert manifest["sourceSha"] == "a" * 40
    assert "/mac-installer-17-2/" in manifest["launcherUrl"]
    assert run("bootstrap", SOURCE_COMMIT="../bad").returncode != 0
    assert run("manifest", RELEASE_TAG="windows-installer-17-2").returncode != 0
print("PASS release metadata, minimum macOS, asset hash, and invalid input checks")
