# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec, run separately on each OS (Windows produces a
.exe, Linux produces a onefile ELF binary, macOS produces a proper .app
bundle via onedir+BUNDLE -- onefile+BUNDLE is deprecated/broken for windowed
macOS apps). Build with:  pyinstaller packaging/pyinstaller/aws-cred-manager.spec
run from the project root.
"""
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).parent.parent  # project root (this file lives in packaging/pyinstaller/)
SRC = ROOT / "src"
ICONS = SRC / "aws_cred_manager" / "icons"

# Single source of truth for the version: src/aws_cred_manager/__init__.py.
# Read it as text (not imported) so the spec never depends on the package
# being importable at build time. Feeds the macOS About panel below.
import re
VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"', (SRC / "aws_cred_manager" / "__init__.py").read_text()
).group(1)

is_macos = sys.platform == "darwin"
is_windows = sys.platform == "win32"

datas = [(str(ICONS), "aws_cred_manager/icons")]
hiddenimports = ["botocore", "boto3"]
if not is_macos:
    hiddenimports += ["pystray", "PIL._tkinter_finder"]

a = Analysis(
    [str(Path(SPECPATH) / "entrypoint.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon_path = None
if is_windows:
    icon_path = str(ICONS / "icon.ico")
elif is_macos:
    icon_path = str(ICONS / "icon.icns")

if is_macos:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="AWS Credential Manager",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=icon_path,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name="AWS Credential Manager",
    )
    app = BUNDLE(
        coll,
        name="AWS Credential Manager.app",
        icon=icon_path,
        bundle_identifier="com.awscredmanager.app",
        info_plist={
            "CFBundleName": "AWS Credential Manager",
            "CFBundleDisplayName": "AWS Credential Manager",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # Regular (Dock-visible, foreground) app -- no LSUIElement.
            # This is a normal window-based app now, not a menu-bar-only
            # accessory app, so it needs none of the activation-policy
            # juggling a menu-bar app would require.
            "LSUIElement": False,
            "NSHumanReadableCopyright": "AWS Credential Manager",
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="aws-cred-manager",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        icon=icon_path,
    )
