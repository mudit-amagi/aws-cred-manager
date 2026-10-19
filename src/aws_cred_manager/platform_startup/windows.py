"""Windows startup registration via the per-user Run registry key.

HKCU is used (not HKLM) so no admin rights are required -- consistent with
this being a per-user credential tool, not a system-wide install.
"""
from __future__ import annotations

import winreg

from ._launch_target import executable_and_args

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "AWSCredManager"


def _command_line() -> str:
    exe, args = executable_and_args(["--background"])
    parts = [f'"{exe}"'] + [f'"{a}"' if " " in a else a for a in args]
    return " ".join(parts)


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def enable() -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _command_line())


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
    except FileNotFoundError:
        pass
