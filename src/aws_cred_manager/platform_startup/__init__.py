"""Register / unregister this app to launch at OS login, running hidden
(--background) so it starts straight into the background refresh loop
without popping the window open unasked.
"""
from __future__ import annotations

import sys


def _impl():
    if sys.platform == "win32":
        from . import windows as impl
    elif sys.platform == "darwin":
        from . import macos as impl
    else:
        from . import linux as impl
    return impl


def is_enabled() -> bool:
    return _impl().is_enabled()


def enable() -> None:
    _impl().enable()


def disable() -> None:
    _impl().disable()
