"""macOS startup registration via a per-user LaunchAgent plist.

A LaunchAgent (not a Login Item) is used because it's plain XML written to
a well-known directory -- no Apple Events / System Events automation
permission prompt required, unlike the modern SMAppService login-item API
or AppleScript-based approaches.
"""
from __future__ import annotations

from pathlib import Path

from ._launch_target import executable_and_args

_LABEL = "com.awscredmanager.agent"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def is_enabled() -> bool:
    return _plist_path().exists()


def enable() -> None:
    exe, args = executable_and_args(["--background"])
    args_xml = "\n".join(f"        <string>{a}</string>" for a in args)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
{args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
"""
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist)


def disable() -> None:
    path = _plist_path()
    if path.exists():
        path.unlink()
