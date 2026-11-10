"""Native Mac packaging metadata. Does not build or run the orders application."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sys

root = Path(__file__).resolve().parent
settings = json.loads((root / "release-settings.json").read_text())
commit = os.environ["SOURCE_COMMIT"]
tag = os.environ["RELEASE_TAG"]
if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"mac-installer-[0-9]+-[0-9]+", tag):
    raise SystemExit("Invalid source commit or release tag")
minimum = max((settings["dotnetMinimumMacOS"], settings["dockerMinimumMacOS"]), key=lambda v: tuple(map(int, v.split("."))))
bundle = Path(sys.argv[2])
manifest = dict(schemaVersion=1, sourceSha=commit, architecture="arm64", minimumMacOS=minimum,
                helperProtocolVersion=1, releaseTag=tag)
if sys.argv[1] == "bootstrap":
    (bundle / "Contents/Resources/bootstrap.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with (bundle / "Contents/Info.plist").open("wb") as out:
        plistlib.dump(dict(CFBundleIdentifier=settings["bundleIdentifier"], CFBundleName="Order Manager",
                          CFBundleDisplayName="Order Manager", CFBundleExecutable="OrderManager", CFBundlePackageType="APPL",
                          CFBundleShortVersionString="1.0", CFBundleVersion=tag.removeprefix("mac-installer-").replace("-", "."),
                          LSMinimumSystemVersion=minimum, LSUIElement=True, NSHighResolutionCapable=True), out)
elif sys.argv[1] == "manifest":
    output = root / "build/output"
    manifest.update(launcherUrl=f"https://github.com/penner42/order-management/releases/download/{tag}/OrderManager-arm64.zip",
                    launcherSha256=hashlib.sha256((output / "OrderManager-arm64.zip").read_bytes()).hexdigest())
    (output / "mac-release.json").write_text(json.dumps(manifest, indent=2) + "\n")
else:
    raise SystemExit("Expected bootstrap or manifest")
