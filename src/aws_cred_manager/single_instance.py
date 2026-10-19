"""Single-instance lock + local IPC over a loopback TCP socket.

Binding 127.0.0.1:IPC_PORT doubles as both the mutex (a second process's
bind fails immediately if the first is still running) and the signal
channel (the second process connects to the same port and sends a short
message asking the first, already-running process to show its window).

This intentionally does not use anything OS-specific (named mutex, D-Bus,
etc.) so the same code works unchanged on Windows, macOS, and Linux.
"""
from __future__ import annotations

import logging
import socket
import threading
from typing import Callable, Optional

from . import app_paths

logger = logging.getLogger(__name__)

_REQUEST_SHOW_WINDOW = b"SHOW\n"


def try_become_primary_instance(on_show_window_request: Callable[[], None]) -> Optional[socket.socket]:
    """Attempt to bind the single-instance port. On success, starts a daemon
    thread accepting connections and invoking on_show_window_request() for
    each SHOW message, and returns the bound server socket (keep a
    reference for the process lifetime -- letting it get garbage collected
    would close the socket and release the lock). Returns None if another
    instance already holds the lock.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((app_paths.IPC_HOST, app_paths.IPC_PORT))
    except OSError:
        server.close()
        return None

    server.listen(5)

    def _accept_loop():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return  # socket closed (process shutting down)
            try:
                data = conn.recv(64)
                if data.strip() == _REQUEST_SHOW_WINDOW.strip():
                    on_show_window_request()
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    thread = threading.Thread(target=_accept_loop, name="single-instance-ipc", daemon=True)
    thread.start()
    return server


def notify_existing_instance() -> bool:
    """Ask an already-running instance to show its window. Returns True if
    the message was delivered.
    """
    try:
        with socket.create_connection((app_paths.IPC_HOST, app_paths.IPC_PORT), timeout=2) as conn:
            conn.sendall(_REQUEST_SHOW_WINDOW)
        return True
    except OSError:
        logger.warning("Could not reach an existing instance on port %s", app_paths.IPC_PORT)
        return False
