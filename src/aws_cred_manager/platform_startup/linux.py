"""Linux startup registration via an XDG autostart .desktop file."""
from __future__ import annotations

from pathlib import Path

from ._launch_target import executable_and_args

_DESKTOP_FILE = "aws-cred-manager.desktop"


def _autostart_dir() -> Path:
    import os

    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart"


def _desktop_path() -> Path:
    return _autostart_dir() / _DESKTOP_FILE


def is_enabled() -> bool:
    return _desktop_path().exists()


def enable() -> None:
    exe, args = executable_and_args(["--background"])
    exec_line = " ".join([exe, *args])
    content = f"""[Desktop Entry]
Type=Application
Name=AWS Credential Manager
Comment=Keeps AWS SSO credentials refreshed in the background
Exec={exec_line}
X-GNOME-Autostart-enabled=true
NoDisplay=false
Terminal=false
"""
    path = _desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def disable() -> None:
    path = _desktop_path()
    if path.exists():
        path.unlink()
