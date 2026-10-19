#!/usr/bin/env bash
# Build the macOS .app and wrap it into a distributable .dmg.
# Run from the project root on a real Mac: bash packaging/macos/build.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

python3 -m venv .venv-build 2>/dev/null || true
source .venv-build/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt pyinstaller >/dev/null

rm -rf build dist
pyinstaller packaging/pyinstaller/aws-cred-manager.spec --noconfirm

APP="dist/AWS Credential Manager.app"

# Ad-hoc codesign: not a verified developer signature, but it satisfies
# Gatekeeper's baseline requirement so the app can be opened normally
# (right-click Open once) instead of needing `xattr -cr` in Terminal.
codesign --force --deep --sign - "$APP"

DMG_DIR="dist/dmg"
rm -rf "$DMG_DIR" && mkdir -p "$DMG_DIR"
cp -R "$APP" "$DMG_DIR/"
ln -s /Applications "$DMG_DIR/Applications"

hdiutil create -volname "AWS Credential Manager" \
  -srcfolder "$DMG_DIR" \
  -ov -format UDZO \
  "dist/AWS-Credential-Manager.dmg"

echo ""
echo "Built: dist/AWS-Credential-Manager.dmg"
