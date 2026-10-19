"""Shared helper: the exact command line to relaunch this app, used by all
three platform_startup backends and by single-instance relaunch/tray code.
"""
from __future__ import annotations

import sys

from .. import app_paths


def executable_and_args(extra_args=()) -> tuple[str, list[str]]:
    """Returns (executable, args) such that
    subprocess.Popen([executable, *args]) relaunches this app.
    """
    if app_paths.is_frozen():
        return sys.executable, list(extra_args)
    return sys.executable, ["-m", "aws_cred_manager", *extra_args]
