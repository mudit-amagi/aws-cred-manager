"""Windows/Linux system tray icon.

Runs via pystray's run_detached(), which starts the icon's event loop on
its own background thread -- the Tk mainloop stays on the main thread the
whole time. macOS does not use this module at all (see gui/app.py): it
runs as a regular Dock app instead of a menu-bar app.

Every callback below fires on pystray's thread, never Tk's. Anything that
touches the Tk window is marshaled onto the Tk main thread via
window.after(0, ...) -- calling Tk methods directly from another thread is
undefined behavior.
"""
from __future__ import annotations

import logging
from typing import Callable

import pystray

from . import config_store, platform_startup, tray_icons

logger = logging.getLogger(__name__)


class TrayController:
    def __init__(self, window, worker, on_quit: Callable[[], None]):
        self._window = window
        self._worker = worker
        self._on_quit = on_quit
        self._status = "idle"
        self._base_image = tray_icons.base_icon_image()

        self.icon = pystray.Icon(
            "aws-cred-manager",
            icon=self._image_for_status("idle"),
            title="AWS Credential Manager",
            menu=self._build_menu(),
        )

    def _image_for_status(self, status: str):
        return tray_icons.status_badge(self._base_image, tray_icons.status_key_for(status))

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(lambda item: self._status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Manager", self._on_open),
            pystray.MenuItem("Refresh Now", self._on_refresh_now),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start on Login",
                self._on_toggle_start_on_login,
                checked=lambda item: platform_startup.is_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit_clicked),
        )

    def _status_text(self) -> str:
        profiles = config_store.load_profiles()
        if not config_store.load_settings().is_configured():
            return "Not configured -- open Manager"
        if not profiles:
            return "No profiles configured"
        labels = {
            "ok": f"OK -- {len(profiles)} profile(s) refreshed",
            "refreshing": "Refreshing...",
            "needs_login": "Sign-in required",
            "partial_error": "Some profiles failed",
            "idle": "Idle",
        }
        return labels.get(self._status, self._status[:60])

    # -- called from app.py's on_status, already on pystray-safe thread -----

    def on_status(self, status: str) -> None:
        self._status = status
        self.icon.icon = self._image_for_status(status)
        self.icon.menu = self._build_menu()

    def notify_device_auth(self, user_code: str) -> None:
        try:
            self.icon.notify(f"Confirm code {user_code} in your browser to finish signing in.", "AWS sign-in required")
        except Exception:
            logger.exception("Failed to show tray notification")

    # -- menu actions (pystray thread) ---------------------------------

    def _on_open(self, icon=None, item=None) -> None:
        self._window.after(0, self._window.show)

    def _on_refresh_now(self, icon=None, item=None) -> None:
        self._worker.refresh_now(force_login=True)

    def _on_toggle_start_on_login(self, icon=None, item=None) -> None:
        if platform_startup.is_enabled():
            platform_startup.disable()
        else:
            platform_startup.enable()

    def _on_quit_clicked(self, icon=None, item=None) -> None:
        self._window.after(0, self._on_quit)

    # -- lifecycle ------------------------------------------------------

    def run_detached(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
