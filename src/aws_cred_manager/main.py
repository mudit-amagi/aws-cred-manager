"""Single entry point for every platform.

--background is the only flag: it means "start without showing the window"
and is what the login-item/LaunchAgent/autostart registration passes so a
login-time launch doesn't pop the window open unasked. A normal launch
(double-clicking the app, or running with no arguments) shows the window
immediately.
"""
from __future__ import annotations

import argparse

from .gui.app import run_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="aws-cred-manager")
    parser.add_argument(
        "--background", action="store_true",
        help="Start without showing the window (used by the login-item launch).",
    )
    args = parser.parse_args()
    run_app(start_hidden=args.background)


if __name__ == "__main__":
    main()
