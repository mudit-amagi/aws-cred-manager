"""Top-level orchestration: one process, one Tk window, one background
refresh thread, and (Windows/Linux only) one tray icon -- wired together
and run for the lifetime of the app.

Startup sequence:
  1. Try to become the single-instance primary (bind the loopback lock
     port). If another instance already holds it, just ask it to show its
     window and exit immediately -- this process does nothing else.
  2. Build the Tk window (created hidden; shown afterwards unless
     --background was passed, e.g. by the login-item launch).
  3. Start the background refresh worker thread.
  4. On Windows/Linux, build and start the tray icon on its own thread.
  5. Enter the Tk mainloop. Everything else (refresh worker, tray, IPC
     accept loop) runs on background threads for the rest of the process's
     life; the Tk mainloop is the only thing on the main thread.
"""
from __future__ import annotations

import logging
import sys

from .. import app_paths, aws_sso, refresh_worker, single_instance
from .window import AppWindow

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    app_paths.app_data_dir()
    logging.basicConfig(
        filename=str(app_paths.log_file()),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run_app(start_hidden: bool) -> None:
    _setup_logging()
    logger.info("Starting (start_hidden=%s, platform=%s, frozen=%s)", start_hidden, sys.platform, app_paths.is_frozen())

    try:
        _run(start_hidden)
    except Exception:
        # A windowed (console-less) build has nowhere visible to print an
        # unhandled traceback -- without this, a startup crash would be
        # completely silent to the user. This is the difference between
        # "nothing happened, no idea why" and an actual answer in app.log.
        logger.exception("Fatal error -- app is exiting")
        raise


def _run(start_hidden: bool) -> None:
    window_holder: dict = {}

    def _on_show_request() -> None:
        window = window_holder.get("window")
        if window is not None:
            window.after(0, window.show)

    lock = single_instance.try_become_primary_instance(_on_show_request)
    if lock is None:
        if single_instance.notify_existing_instance():
            logger.info("Another instance is already running; asked it to show its window. Exiting this launch.")
        else:
            logger.warning("Single-instance lock is held but the existing instance did not respond (stale lock?).")
        return

    window = AppWindow(on_quit=lambda: _quit(window, worker, tray))
    window_holder["window"] = window

    tray = None

    def _on_device_auth(info: aws_sso.DeviceAuthInfo) -> None:
        logger.info("Device auth required, code=%s", info.user_code)
        window.after(0, lambda: window.show_auth_code(info.user_code))
        window.after(0, window.show)
        if tray is not None:
            tray.notify_device_auth(info.user_code)

    def _on_status(status: str) -> None:
        window.after(0, lambda: window.on_worker_status(status))
        if tray is not None:
            tray.on_status(status)

    worker = refresh_worker.RefreshWorker(on_device_auth=_on_device_auth, on_status=_on_status)
    window.attach_worker(worker)
    worker.start()

    if sys.platform in ("win32", "linux"):
        from ..tray import TrayController

        tray = TrayController(window, worker, on_quit=lambda: _quit(window, worker, tray))
        tray.run_detached()

    if not start_hidden:
        window.show()

    logger.info("Entering Tk mainloop.")
    window.mainloop()
    logger.info("Tk mainloop exited.")


def _quit(window: AppWindow, worker: "refresh_worker.RefreshWorker", tray) -> None:
    logger.info("Quitting.")
    worker.stop()
    if tray is not None:
        tray.stop()
    window.destroy()
