#!/usr/bin/env bash
# Build the Linux onefile binary and wrap it into a terminal-free AppImage.
# Run from the project root: bash packaging/linux/build.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

python3 -m venv .venv-build 2>/dev/null || true
source .venv-build/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt pyinstaller >/dev/null

rm -rf build dist
pyinstaller packaging/pyinstaller/aws-cred-manager.spec --noconfirm

APPDIR="dist/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp dist/aws-cred-manager "$APPDIR/usr/bin/"
cp src/aws_cred_manager/icons/icon.png "$APPDIR/aws-cred-manager.png"

cat > "$APPDIR/aws-cred-manager.desktop" << DESKTOP
[Desktop Entry]
Type=Application
Name=AWS Credential Manager
Exec=aws-cred-manager
Icon=aws-cred-manager
Categories=Utility;
Terminal=false
DESKTOP

cat > "$APPDIR/AppRun" << 'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/aws-cred-manager" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

if [ ! -f appimagetool.AppImage ]; then
  echo "Downloading appimagetool..."
  curl -L -o appimagetool.AppImage \
    https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x appimagetool.AppImage
fi

ARCH=x86_64 ./appimagetool.AppImage "$APPDIR" "dist/AWS-Credential-Manager-x86_64.AppImage"

echo ""
echo "Built: dist/AWS-Credential-Manager-x86_64.AppImage (double-clickable, no terminal needed)"
echo "Also built: dist/aws-cred-manager (plain onefile binary, for advanced/headless use)"
