"""Background thread that keeps managed profiles' credentials fresh.

Runs for the whole lifetime of the app (started once, in the single
long-lived process), independent of whether the window is currently shown
or hidden. Talks back to the UI only through plain callbacks -- it never
touches a GUI toolkit directly, so it's safe to run on any thread.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from . import aws_sso, config_store, sync

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60


def any_profile_needs_refresh(profiles) -> bool:
    if not profiles:
        return False
    for p in profiles:
        if p.paused:
            continue
        if p.last_expiration_epoch is None:
            return True
        if aws_sso.is_expired(p.last_expiration_epoch):
            return True
    return False


class RefreshWorker:
    """Status values pushed via on_status(): "idle", "refreshing", "ok",
    "all_paused", "needs_login", "partial_error", "error:<message>".

    "all_paused" means every managed profile currently has its own
    `paused` flag set (whether via the toolbar's bulk Pause All, or by
    individually pausing every row one at a time -- those are the same
    thing, see config_store.set_all_profiles_paused). Individually
    paused profiles otherwise are just quietly skipped and fold into
    "ok" for the rest.
    """

    def __init__(
        self,
        on_device_auth: Callable[[aws_sso.DeviceAuthInfo], None],
        on_status: Callable[[str], None],
        poll_interval_seconds: int = POLL_INTERVAL_SECONDS,
    ):
        self._on_device_auth = on_device_auth
        self._on_status = on_status
        self._poll_interval = poll_interval_seconds
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._busy_lock = threading.Lock()
        # Set by refresh_now(force_login=True) to interrupt a sign-in that's
        # currently blocked waiting for browser approval -- without this, a
        # stale attempt (tab closed, or just ignored) occupies the worker
        # thread until it times out on its own (up to 10 minutes), and every
        # Refresh Now click in that window is silently queued behind it. See
        # aws_sso.login()'s cancel_event parameter for where this is used.
        self._cancel_event = threading.Event()
        # Set when a sign-in attempt timed out / was denied. While set (and
        # no valid SSO token exists), the periodic poll does NOT start a new
        # sign-in -- doing so opened a fresh browser tab on every tick for as
        # long as the user ignored the first one. Only an explicit Refresh
        # Now (force_login) retries; a valid token appearing (e.g. signed in
        # from the Add Row dialog) also clears it.
        self._login_blocked = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="refresh-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def refresh_now(self, force_login: bool = False) -> None:
        if force_login:
            # Abort any sign-in still polling for approval from an earlier
            # force_login call -- otherwise this request just queues behind
            # it (see _cancel_event's comment in __init__).
            self._cancel_event.set()
        self._pending_force_login = force_login
        self._wake_event.set()

    _pending_force_login = False

    def _loop(self) -> None:
        # Do one pass immediately on startup rather than waiting a full
        # poll interval, so credentials are fresh right after the app
        # launches instead of only after the first tick.
        self._run_once(force_login=False)
        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=self._poll_interval)
            if self._stop_event.is_set():
                return
            force_login = self._pending_force_login
            self._pending_force_login = False
            self._wake_event.clear()
            self._run_once(force_login=force_login)

    def _run_once(self, force_login: bool) -> None:
        if not self._busy_lock.acquire(blocking=False):
            return  # a refresh is already in flight
        try:
            settings = config_store.load_settings()
            profiles = config_store.load_profiles()
            if not settings.is_configured() or not profiles:
                self._on_status("idle")
                return
            if all(p.paused for p in profiles):
                self._on_status("all_paused")
                return
            if not force_login and not any_profile_needs_refresh(profiles):
                self._on_status("ok")
                return

            if force_login:
                self._login_blocked = False
            elif self._login_blocked:
                if aws_sso.get_cached_token(settings.sso_session_name) is None:
                    self._on_status("needs_login")
                    return
                self._login_blocked = False

            # Clear before use: this run is about to become "the" in-flight
            # attempt, so it must not immediately cancel itself. A refresh_now()
            # call that arrives after this point sets it again to interrupt
            # *this* run, which is exactly what should happen.
            self._cancel_event.clear()
            self._on_status("refreshing")
            try:
                result = sync.refresh_credentials(
                    settings, profiles, self._on_device_auth, force_login=force_login,
                    cancel_event=self._cancel_event,
                )
            except (aws_sso.LoginCancelled, aws_sso.LoginTimedOut):
                self._login_blocked = True
                self._on_status("needs_login")
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("Refresh failed")
                self._on_status(f"error:{exc}")
                return

            if result.errors:
                logger.warning("Refresh completed with errors: %s", result.errors)
                self._on_status("partial_error")
            else:
                self._on_status("ok")
        finally:
            self._busy_lock.release()
