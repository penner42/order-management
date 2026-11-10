#!/bin/bash
# Native Apple tools are used only for the launcher and installer, never the orders app.
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { echo 'Package on an Apple Silicon Mac.' >&2; exit 1; }
: "${SOURCE_COMMIT:?Set SOURCE_COMMIT to the full source commit}"
: "${RELEASE_TAG:?Set RELEASE_TAG to mac-installer-<run>-<attempt>}"
[ -x macos/build/helper/OrderManager.Mac ] || { echo 'Download the Docker-built helper artifact to macos/build/helper first.' >&2; exit 1; }
output="$PWD/macos/build/output"
bundle="$PWD/macos/build/staging/Order Manager.app"
mkdir -p "$output"
rm -rf "$PWD/macos/build/staging"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources" "$bundle/Contents/Helpers"
cp -R macos/build/helper/. "$bundle/Contents/Helpers/"
python3 macos/release.py bootstrap "$bundle"
minimum=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$bundle/Contents/Info.plist")
export MACOSX_DEPLOYMENT_TARGET="$minimum"
swift test --package-path macos/App --arch arm64
swift build --package-path macos/App -c release --arch arm64
bin_path=$(swift build --package-path macos/App -c release --arch arm64 --show-bin-path)
cp "$bin_path/OrderManager" "$bundle/Contents/MacOS/OrderManager"
# Sign nested runtime libraries first. No Developer ID, account, or notarization is used.
while IFS= read -r -d '' binary; do
  if file "$binary" | grep -q 'Mach-O'; then codesign --force --sign - "$binary"; fi
done < <(find "$bundle/Contents/Helpers" -type f -print0)
codesign --force --sign - "$bundle/Contents/MacOS/OrderManager"
codesign --force --sign - "$bundle"
codesign --verify --deep --strict "$bundle"
lipo -verify_arch arm64 "$bundle/Contents/MacOS/OrderManager"
lipo -verify_arch arm64 "$bundle/Contents/Helpers/OrderManager.Mac"
# The self-check command must launch and produce a structured completion. No Docker setup or services are started.
helper_home=$(mktemp -d)
trap 'rm -rf "$helper_home"' EXIT
# The self-check does not create application state or register startup.
"$bundle/Contents/Helpers/OrderManager.Mac" self-check > "$helper_home/helper-check.jsonl"
grep -q '"type":"complete"' "$helper_home/helper-check.jsonl"
ditto --norsrc --noextattr -c -k --keepParent "$bundle" "$output/OrderManager-arm64.zip"
python3 macos/release.py manifest "$bundle"
cp macos/INSTALL.txt "$PWD/macos/build/staging/READ ME FIRST.txt"
hdiutil create -volname 'Order Manager' -srcfolder "$PWD/macos/build/staging" -ov -format UDZO "$output/OrderManager-Setup-arm64.dmg"
(cd "$output" && shasum -a 256 OrderManager-arm64.zip OrderManager-Setup-arm64.dmg mac-release.json > SHA256SUMS)
